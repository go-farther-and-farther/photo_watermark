from setuptools import setup, find_packages

setup(
    name="photo-watermark",
    version="1.5.0",
    author="go-farther-and-farther",
    author_email="",
    description="相机照片水印边框生成器 - 自动读取EXIF信息，生成专业水印边框",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/go-farther-and-farther/photo_watermark",
    py_modules=["photo_watermark"],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Multimedia :: Graphics",
    ],
    python_requires=">=3.6",
    install_requires=[
        "Pillow>=10.0.0",
        "exifread>=3.0.0",
        "tkinterdnd2>=0.4.0",
    ],
    entry_points={
        "console_scripts": [
            "photo-watermark=photo_watermark:main",
        ],
    },
    include_package_data=True,
    package_data={
        "": ["*.png"],
    },
)
