from setuptools import setup

setup(
    name='LambdaCheck',
    include_package_data=True,
    packages=['lambdacheck'],
    zip_safe=False,
    entry_points={
        'console_scripts': [
            'hello=lambdacheck.cli:Hello',
        ],
    },
)
