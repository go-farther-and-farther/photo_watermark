# -*- coding: utf-8 -*-
"""BUG 回归测试：
1) refresh_globals_from_ini 应同步「白条边框/边框高度」→ BORDER_HEIGHT_RATIO
   （设置窗口保存后需立即生效，而不是重启后才生效）
2) exif_reader 的 ExifTool 引擎必须请求 -Make，
   且机型清洗应产出「品牌 型号」（如 SONY ZV-E10），否则丢失品牌前缀
"""
import os
import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SONY_JPG = PROJECT_ROOT / "input" / "Sony.JPG"
needs_sony = pytest.mark.skipif(not SONY_JPG.exists(), reason="缺少 Sony 测试样张")


@pytest.fixture
def wm(monkeypatch):
    """导入 photo_watermark，并把配置目录指到仓库内临时目录（测完清理，不污染真实配置）。
    注：不使用 pytest 的 tmp_path——在某些受限环境下系统临时目录不可写。"""
    import photo_watermark as pw
    tmp_path = Path(pw.__file__).parent / f"_test_tmp_{os.getpid()}"
    tmp_path.mkdir(exist_ok=True)
    src_ini = Path(pw.__file__).parent / "水印设置.ini"
    if src_ini.exists():
        shutil.copy(src_ini, tmp_path / "水印设置.ini")
    monkeypatch.setattr(pw, "get_base_dir", lambda: tmp_path)
    yield pw, tmp_path
    shutil.rmtree(tmp_path, ignore_errors=True)


def _write_user_setting(tmp_path, section, key, value):
    # 复用生产写入路径（update_ini_value），保证测试与真实持久化行为一致；
    # 旧的本地 ConfigParser 副本缺 interpolation=None/optionxform=str，
    # 与生产实现已经分叉（值含 % 时助手自身会崩）
    pw_mod = sys.modules.get("photo_watermark")
    assert pw_mod is not None, "须先经 wm fixture 导入 photo_watermark"
    assert pw_mod.update_ini_value(section, key, value), \
        f"update_ini_value 写入失败: {section}/{key}={value!r}"


def test_refresh_globals_syncs_strip_border_height(wm):
    """保存了「白条边框/边框高度」后，refresh 必须立即更新 BORDER_HEIGHT_RATIO"""
    pw, tmp_path = wm
    _write_user_setting(tmp_path, "白条边框", "边框高度", "0.12")

    pw.refresh_globals_from_ini()

    assert abs(pw.BORDER_HEIGHT_RATIO - 0.12) < 1e-9, (
        f"设置窗口改了白条边框高度后本次会话不生效：BORDER_HEIGHT_RATIO="
        f"{pw.BORDER_HEIGHT_RATIO}"
    )


def test_refresh_globals_syncs_other_style_settings(wm):
    """顺带核对几个设置窗口确实会写、refresh 也该同步的关键项"""
    pw, tmp_path = wm
    _write_user_setting(tmp_path, "白条边框", "Logo高度", "0.9")
    _write_user_setting(tmp_path, "官方字标", "字标大小", "0.055")

    pw.refresh_globals_from_ini()

    assert abs(pw.LOGO_HEIGHT_RATIO - 0.9) < 1e-9
    assert abs(pw.WORDMARK_RATIO - 0.055) < 1e-9


@needs_sony
def test_exiftool_requests_make_for_brand_prefix():
    """ExifTool 不带 -Make 时 entry.get('Make') 永远为空 → 机型丢品牌前缀。
    修复后：SONY 照片的 model 应是 'SONY ZV-E10'。"""
    from exif_reader import _EXIFTOOL_FIELDS, find_exiftool, read_exif_file

    assert "-Make" in _EXIFTOOL_FIELDS, (
        "_EXIFTOOL_FIELDS 缺少 -Make，导致品牌清洗永远拿不到 Make"
    )

    exe = find_exiftool()
    if not exe:
        pytest.skip("本机未安装 ExifTool")
    r = read_exif_file(str(SONY_JPG))
    assert r["ok"] is True
    assert r["model"] == "SONY ZV-E10", f"机型应为「SONY ZV-E10」，实际 {r['model']!r}"


def _make_mode_image(tmp_path, mode, fill):
    from PIL import Image
    src = tmp_path / f"in_{mode.replace(';', '_')}.png"
    Image.new(mode, (800, 600), fill).save(str(src), "PNG")
    return str(src)


def test_process_nonrgb_modes_all_styles(wm):
    """灰度+透明(LA)/16位灰度(I;16)等非常规模式进入处理时：
    原实现对 blur 直接抛 wrong-mode、对 art/wordmark 存 JPEG 抛 cannot write mode，
    应在入口统一规范为 RGB 后正常处理。"""
    pw, tmp_path = wm
    cases = [
        ("LA", (128, 200), ["blur", "art", "wordmark"]),
        ("I;16", 12345, ["blur", "art", "wordmark"]),
    ]
    for mode, fill, styles in cases:
        src = _make_mode_image(tmp_path, mode, fill)
        for st in styles:
            out = tmp_path / f"out_{mode.replace(';', '_')}_{st}.jpg"
            status = pw.process_single_image(src, str(out), st, explicit_style=True)
            assert status == "ok", f"{mode} 模式 + {st} 样式应能处理，实际 {status!r}"
            assert out.exists(), f"{mode}+{st} 应产出文件 {out.name}"
            from PIL import Image
            with Image.open(out) as im:
                assert im.mode == "RGB"


def test_render_preview_nonrgb_mode(wm):
    """预览渲染同样只放行 RGBA/P——LA 模式的 blur 预览原来会失败返回 None"""
    pw, tmp_path = wm
    src = _make_mode_image(tmp_path, "LA", (128, 200))
    img = pw.render_preview(src, ["blur"])
    assert img is not None, "LA 模式照片的 blur 预览渲染失败"
    assert img.mode == "RGB"


# ---------- 配置链路回归 ----------

def test_percent_in_config_value_round_trips(wm, monkeypatch):
    """配置值含 % （如「满100减20%」）时，默认 ConfigParser 插值器会在读取阶段抛
    InterpolationSyntaxError——该调用位于模块导入期，表现为程序启动即崩。
    通过真实加载链验证（load_ini_config 构造的解析器必须禁用插值）。"""
    pw, tmp_path = wm

    user_ini = tmp_path / "用户设置.ini"
    user_ini.write_text(
        "[基础设置]\n自定义文字 = 满100减20%\n", encoding="utf-8")
    monkeypatch.setattr(pw, "get_base_dir", lambda: tmp_path)
    monkeypatch.setattr(pw, "resource_path",
                        lambda name: str(tmp_path / name))

    cfg = pw.load_ini_config()          # 真实加载器
    monkeypatch.setattr(pw, "_ini_config", cfg)
    got = pw.get_config_value("基础设置", "自定义文字", "", "str")
    assert got == "满100减20%", f"含%的值应原样读出，实际 {got!r}"


def _build_startup_project(tmp_path, user_ini_lines):
    """搭一个隔离「迷你项目」目录（拷贝入口源码与两份 ini），
    返回一个子进程脚本路径——完整走一遍程序导入期 = 用户真实启动路径。
    注：不能用 importlib.reload 模拟——reload 会重定义 get_base_dir 覆盖补丁。
    ini 直接写文本而非 ConfigParser.set：默认插值器在 set 阶段就会拦截裸 %，
    而磁盘上的真实文件内容不受此限制。"""
    proj = tmp_path / "_startup_proj"
    proj.mkdir(exist_ok=True)
    for fname in ("photo_watermark.py", "compressor.py", "color_grade.py",
                  "config.py", "水印设置.ini"):
        src = PROJECT_ROOT / fname
        if src.exists():
            shutil.copy(src, proj / fname)

    sections = {}
    for section, key, value in user_ini_lines:
        sections.setdefault(section, []).append(f"{key} = {value}")
    with open(proj / "用户设置.ini", "w", encoding="utf-8") as f:
        for section, lines in sections.items():
            f.write(f"[{section}]\n")
            f.write("\n".join(lines))
            f.write("\n\n")

    child = proj / "child_check.py"
    child.write_text(
        "# -*- coding: utf-8 -*-\n"
        "import sys\n"
        f"sys.path.insert(0, r'{proj}')\n"
        "import photo_watermark as p\n"
        "print('BORDER_COLOR=', p.BORDER_FRAME_COLOR)\n"
        "print('BORDER_TEXT_COLOR=', p.BORDER_TEXT_COLOR)\n"
        "print('DEFAULT_TEXT=', repr(p.DEFAULT_TEXT))\n",
        encoding="utf-8")
    return child


def test_startup_survives_percent_and_keeps_settings(wm):
    """端到端启动场景：用户设置同时含 % 文案与自选纯色边框颜色。
    原缺陷：(a) 含 % 的值让导入期直接崩溃；(b) 颜色保存到 [颜色设置]，
    启动却只读 [纯色边框] → 重启后颜色静默回退默认。"""
    import subprocess
    pw, tmp_path = wm

    child = _build_startup_project(tmp_path, [
        ("基础设置", "自定义文字", "满100减20%"),
        ("颜色设置", "边框颜色", "白色"),
        ("颜色设置", "边框文字颜色", "蓝色"),
    ])
    r = subprocess.run([sys.executable, "-X", "utf8", str(child)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    assert r.returncode == 0, (
        "程序导入期不应崩溃（含 % 的用户设置曾导致启动失败）\n"
        f"stderr 尾部:\n{r.stderr[-800:]}"
    )
    out = r.stdout
    assert "满100减20%" in out, f"% 文案应原样生效，stdout={out!r}"
    assert "BORDER_COLOR= (255, 255, 255)" in out, \
        f"用户设置的纯色边框颜色「白」应在重启后保留，stdout={out!r}"
    assert "BORDER_TEXT_COLOR= (0, 0, 255)" in out, \
        f"用户设置的边框文字颜色「蓝」应在重启后保留，stdout={out!r}"


def test_update_ini_value_preserves_key_case(wm):
    """ConfigParser 默认 optionxform 把键名转小写，用户设置.ini 里「JPEG质量」
    被写成「jpeg质量」。修复后写盘应保留原始大小写。"""
    pw, tmp_path = wm
    from pathlib import Path as _P

    assert pw.update_ini_value("基础设置", "JPEG质量", "95")
    assert pw.update_ini_value("白条边框", "Logo高度", "0.8")

    text = (_P(tmp_path) / "用户设置.ini").read_text(encoding="utf-8")
    assert "JPEG质量" in text and "jpeg质量" not in text
    assert "Logo高度" in text


def test_update_ini_value_removes_case_variant_duplicates(wm):
    """旧版写盘的键被 optionxform 折成小写（jpeg质量=98）；新版 optionxform=str
    直接 set 原样键会产生两个键，strict 读取端抛 DuplicateOptionError →
    个人配置整段丢失。写入时必须清理大小写变体旧键。"""
    pw, tmp_path = wm
    from pathlib import Path as _P

    # 模拟旧版写入的小写键
    user_ini = _P(tmp_path) / "用户设置.ini"
    user_ini.write_text("[基础设置]\njpeg质量 = 98\n", encoding="utf-8")

    assert pw.update_ini_value("基础设置", "JPEG质量", "95")

    text = user_ini.read_text(encoding="utf-8")
    assert "jpeg质量" not in text, f"小写旧键应被清理，实际:\n{text}"
    # 清理后可被 strict 读取端正常解析，且新值生效
    cfg = pw.load_ini_config()
    assert cfg.getint("基础设置", "JPEG质量") == 95


def test_load_ini_config_tolerates_duplicate_case_keys(wm):
    """已经损坏（同节含仅大小写不同的重复键）的用户配置：读取端 strict=False
    宽容解析取后值，而不是整文件解析失败导致全部个人设置回退默认。"""
    pw, tmp_path = wm
    user_ini = tmp_path / "用户设置.ini"
    user_ini.write_text(
        "[基础设置]\njpeg质量 = 98\nJPEG质量 = 95\n[白条边框]\nLogo高度 = 0.8\n",
        encoding="utf-8")

    cfg = pw.load_ini_config()
    assert cfg.getint("基础设置", "JPEG质量") == 95
    assert cfg.getfloat("白条边框", "Logo高度") == 0.8


def test_legacy_border_color_promoted_from_old_section(wm):
    """旧模板把边框颜色放 [纯色边框]；默认 ini 固定含 [颜色设置]，若不做
    旧键提升，用户旧颜色会被默认 黑色/白色 提前命中而静默丢失。"""
    pw, tmp_path = wm
    user_ini = tmp_path / "用户设置.ini"
    user_ini.write_text(
        "[纯色边框]\n边框颜色 = 红色\n文字颜色 = 黄色\n", encoding="utf-8")

    cfg = pw.load_ini_config()
    assert cfg.get("颜色设置", "边框颜色") == "红色"
    assert cfg.get("颜色设置", "边框文字颜色") == "黄色"

    import photo_watermark as pwmod
    pwmod._ini_config = cfg
    try:
        assert pwmod.get_config_value("颜色设置", "边框颜色", "黑色") == "红色"
        assert pwmod.parse_color(pwmod.get_config_value("颜色设置", "边框颜色", "黑色")) == (255, 0, 0)
    finally:
        pwmod._ini_config = pwmod.load_ini_config()


def test_fmt_float_keeps_fourth_decimal():
    """万分比字段（如参数字体大小 0.0123）经 fmt_float 三位小数会截成 0.012，
    每次保存都漂移。需要支持指定小数位且默认行为不变。"""
    import photo_watermark as pwmod
    assert pwmod.fmt_float(0.0123, 4) == "0.0123"
    assert pwmod.fmt_float(0.0555, 4) == "0.0555"
    # 默认仍是 3 位（既有调用方不受影响）
    assert pwmod.fmt_float(0.08) == "0.08"
    assert pwmod.fmt_float(1.0) == "1"


def test_blur_style_shadow_not_opaque_black(wm, monkeypatch):
    """blur 样式的"阴影"在 RGB 图上用 (0,0,0,180) 作画，alpha 被丢弃变成
    不透明黑色鬼影字。近白背景下成品底部不应存在纯黑笔画像素。"""
    pw, tmp_path = wm
    from PIL import Image

    src = tmp_path / "white.png"
    Image.new("RGB", (800, 600), (250, 250, 250)).save(str(src), "PNG")

    monkeypatch.setattr(pw, "SMART_STYLE", "off")  # 固定样式，避免智能替换干扰
    out = tmp_path / "out_blur.jpg"
    status = pw.process_single_image(str(src), str(out), "blur",
                                     custom_text="测试签名", explicit_style=True)
    assert status == "ok"

    with Image.open(out) as im:
        rgb = im.convert("RGB")
    # 底部文字区域：原实现必有 (0,0,0) 纯黑鬼影；柔和阴影最低约保留背景 25% 亮度
    box = (0, int(rgb.height * 0.75), rgb.width, rgb.height)
    region = rgb.crop(box)
    pixels = region.tobytes()
    darkest = min(
        (tuple(pixels[i:i + 3]) for i in range(0, len(pixels), 3)),
        key=sum,
    )
    assert sum(darkest) > 60, f"底部文字区出现接近纯黑的鬼影像素 {darkest}"


def test_render_cli_preview_supports_all_styles(wm):
    """命令行 --preview 分支原来只认 4 种样式，art/wordmark 直接 NameError；
    且把整套 CLI kwargs 灌进各 apply_* 函数触发 TypeError。统一入口后全部可用。"""
    pw, tmp_path = wm
    src = tmp_path / "p.jpg"
    from PIL import Image
    Image.open(PROJECT_ROOT / "input" / "demo_photo.jpg").resize(
        (800, 600)).save(str(src), "JPEG")

    cli_kwargs = {"position": "bottom-right", "border_color": None,
                  "text_color": None, "opacity": 128, "quality": 90}
    for st in ("strip", "transparent", "border", "blur", "art", "wordmark"):
        img = pw.render_cli_preview(str(src), st, "签名", logo_path="", **cli_kwargs)
        assert img is not None, f"{st} 样式 CLI 预览失败"
        assert img.size[0] > 0


def test_render_cli_preview_normalizes_nonrgb_modes(wm):
    """预览路径同样要规范非 RGB 模式——原来只放行 RGBA/P，
    I;16（16位灰度）+ blur 会抛 wrong-mode 崩溃（批量路径同期已修，此处漏改）"""
    pw, tmp_path = wm
    src = _make_mode_image(tmp_path, "I;16", 12345)
    img = pw.render_cli_preview(src, "blur", "签名")
    assert img is not None, "I;16 模式照片的 blur CLI 预览失败"
    assert img.mode == "RGB"


def test_render_cli_preview_transparent_matches_batch_text(wm):
    """CLI 预览的 transparent 文字组装必须与批处理一致（原来用 format_exif_text
    的 ' - ' 连接且 custom_text 替换参数；批处理是模板 ' | ' 连接且签名追加）"""
    pw, tmp_path = wm
    from PIL import Image

    src = tmp_path / "p2.jpg"
    Image.new("RGB", (800, 600), (200, 200, 200)).save(str(src), "JPEG")

    exif = {"camera": "NIKON Z 7", "focal_length": "50mm", "aperture": "f/1.8",
            "shutter": "1/250s", "iso": "100", "datetime": "2026:08:16 13:25:18"}
    img_cli = pw.render_cli_preview(str(src), "transparent", "签名")
    img_batch = pw.apply_single_style(
        Image.new("RGB", (800, 600), (200, 200, 200)), "transparent", exif, "签名",
        logo_path="")
    # 两条链路都应能渲染出非空图；文字组装统一后尺寸/模式一致即可作冒烟校验
    assert img_cli is not None and img_batch is not None
    assert img_cli.mode == img_batch.mode == "RGB"


# ---------- EXIF 解析与自动旋转回归 ----------

class _FakeTag:
    """模拟 exifread IfdTag：str() 返回可读文本，values 保存原始数值"""
    def __init__(self, printable, values=None):
        self.printable = printable
        self.values = values if values is not None else [printable]

    def __str__(self):
        return str(self.printable)


def _exif_tags(**overrides):
    tags = {
        "Image Make": _FakeTag("NIKON CORPORATION"),
        "Image Model": _FakeTag("NIKON Z 7"),
        "EXIF LensModel": _FakeTag("NIKKOR Z 50mm f/1.8 S"),
        "EXIF FNumber": _FakeTag("28/5"),
        "EXIF ExposureTime": _FakeTag("10/10"),
        "EXIF ISOSpeedRatings": _FakeTag("100"),
        "EXIF FocalLength": _FakeTag("50/1"),
        "EXIF DateTimeOriginal": _FakeTag("2026:08:16 13:25:18"),
    }
    tags.update(overrides)
    return tags


@pytest.fixture
def patched_exif(monkeypatch):
    """让 photo_watermark.read_exif 读到伪造的 exifread 标签。
    注意：open() 发生在 process_file 之前，因此要用真实存在的图片路径调用。"""
    import photo_watermark as pw
    holder = {"tags": {}}
    monkeypatch.setattr(pw.exifread, "process_file",
                        lambda f, details=False: holder["tags"])
    real_img = PROJECT_ROOT / "input" / "demo_photo.jpg"
    if not real_img.exists():
        pytest.skip("缺少任意可用图片样张")
    holder["path"] = str(real_img)
    return holder


def test_read_exif_orientation_numeric_value_wins(patched_exif):
    """exifread 的 str(tag) 是文本描述；方向应优先读数值（values[0]），
    字符串映射只是无数值时的兜底"""
    import photo_watermark as pwmod
    patched_exif["tags"] = _exif_tags(
        **{"Image Orientation": _FakeTag("Mirrored horizontal then rotated 90 CCW", [5])})
    data = pwmod.read_exif(patched_exif["path"])
    assert data["orientation"] == 5


def test_read_exif_orientation_mirrored_strings(patched_exif):
    """镜像族方向的文本描述按 exifread 3.x 实际措辞映射，而不是全部回退成 1"""
    import photo_watermark as pwmod
    cases = {
        "Mirrored horizontal": 2,
        "Mirrored vertical": 4,
        "Mirrored horizontal then rotated 90 CCW": 5,
        "Mirrored horizontal then rotated 90 CW": 7,
    }
    for text, want in cases.items():
        patched_exif["tags"] = _exif_tags(**{"Image Orientation": _FakeTag(text)})
        data = pwmod.read_exif(patched_exif["path"])
        assert data["orientation"] == want, f"{text!r} 应映射为方向 {want}"


class _Ratio:
    """模拟 exifread 的 Ratio 标签（暴露 num/den，区别于 fractions.Fraction）"""
    def __init__(self, num, den):
        self.num, self.den = num, den

    def __str__(self):
        return f"{self.num}/{self.den}"


def test_read_exif_shutter_one_second(patched_exif):
    """曝光恰为 1 秒时应显示「1s」而不是「1/1s」（exifread 会把 1s 约分成 1/1）"""
    import photo_watermark as pwmod
    patched_exif["tags"] = _exif_tags(**{"EXIF ExposureTime": _Ratio(1, 1)})
    data = pwmod.read_exif(patched_exif["path"])
    assert data["shutter"] == "1s"


def test_read_exif_survives_zero_denominator_focal(patched_exif):
    """畸形 EXIF（焦距分母为 0）不应中断整个解析——拍摄时间等仍要读到"""

    import photo_watermark as pwmod
    patched_exif["tags"] = _exif_tags(
        **{"EXIF FocalLength": _Ratio(50, 0),
           "EXIF ExposureTime": _Ratio(10, 10)})
    data = pwmod.read_exif(patched_exif["path"])
    # datetime 保留 EXIF 原始值（冒号格式由显示层再转换）；解析未被中断即可
    assert data["datetime"].startswith("2026:"), "分母为0的焦距不应吞掉后续字段解析"


def test_auto_rotate_matches_pillow_reference():
    """auto_rotate_image 各方向的复合变换必须与 Pillow exif_transpose 权威映射一致
    （原实现 5/7 互为镜像）"""
    import random
    import photo_watermark as pwmod
    from PIL import Image
    random.seed(42)
    img = Image.new("RGB", (37, 23))
    img.putdata([(random.randint(0, 255), i % 256, (i * 7) % 256)
                 for i in range(37 * 23)])

    expect_ops = {
        2: Image.Transpose.FLIP_LEFT_RIGHT,
        3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.FLIP_TOP_BOTTOM,
        5: Image.Transpose.TRANSPOSE,
        6: Image.Transpose.ROTATE_270,
        7: Image.Transpose.TRANSVERSE,
        8: Image.Transpose.ROTATE_90,
    }
    for orientation, op in expect_ops.items():
        got = pwmod.auto_rotate_image(img, orientation).tobytes()
        want = img.transpose(op).tobytes()
        assert got == want, f"方向 {orientation} 的旋转变换与 Pillow 权威实现不一致"


def test_bad_typed_ini_value_falls_back(wm, monkeypatch):
    """手编 ini 把数字项写坏（如「JPEG质量 = abc」）：typed getter 不应抛异常
    炸掉启动，而应回退默认值继续运行。"""
    import configparser as _cp
    pw, _ = wm

    cp = _cp.ConfigParser()
    cp.add_section("基础设置")
    cp.set("基础设置", "JPEG质量", "abc")
    monkeypatch.setattr(pw, "_ini_config", cp)

    got = pw.get_config_value("基础设置", "JPEG质量", 98, "int")
    assert got == 98, f"坏数字应回退 fallback 98，实际 {got!r}"


def test_split_selected_paths_separates_files_and_dirs(wm):
    """GUI 多选混合「文件夹+照片」时必须分类——原来全按图片处理导致逐个报错"""
    pw, wm_tmp = wm
    from PIL import Image

    d1 = wm_tmp / "相册A"
    d1.mkdir()
    Image.new("RGB", (400, 300), (200, 100, 50)).save(str(wm_tmp / "单张.jpg"))
    files, dirs = pw.split_selected_paths([str(d1), str(wm_tmp / "单张.jpg")])
    assert len(files) == 1 and files[0].suffix.lower() == ".jpg"
    assert len(dirs) == 1 and dirs[0].name == "相册A"
    # 不存在的路径两边都不进
    files2, dirs2 = pw.split_selected_paths([str(wm_tmp / "不存在.jpg")])
    assert files2 == [] and dirs2 == []
