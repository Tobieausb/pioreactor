# -*- coding: utf-8 -*-
from __future__ import annotations

from setuptools import find_packages
from setuptools import setup


setup(
    name="pioreactor-nir-spectrometer-plugin",
    version="0.2.0",
    license="MIT",
    license_files=("LICENSE.txt",),
    description="Paired AS7341 NIR transmission and blue-excited 515 nm optical sweeps for Pioreactor.",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    author="Tobias Ebbing",
    url="https://github.com/Tobieausb/pioreactor",
    packages=find_packages(),
    include_package_data=True,
    install_requires=["spectrometer-reading-plugin>=0.4.0"],
    entry_points={"pioreactor.plugins": "nir_spectrometer_plugin = nir_spectrometer_plugin"},
    python_requires=">=3.11",
)
