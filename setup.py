from setuptools import setup, find_packages

setup(
    name="aetherbond",
    version="1.0.0",
    description="A modular open-source Speedify-like multipath aggregation system",
    author="Antigravity Team",
    packages=find_packages(),
    install_requires=[
        "fastapi>=0.100.0",
        "uvicorn>=0.22.0",
        "websockets>=11.0.0",
        "aiohttp>=3.8.0",
        "psutil>=5.9.0",
        "cryptography>=41.0.0",
        "colorama>=0.4.6",
    ],
    entry_points={
        "console_scripts": [
            "aetherbond-client=aetherbond.client.main:main",
            "aetherbond-gui=uvicorn:main",  # Can run uvicorn aetherbond.gui.server:app
        ],
    },
)
