# notes on packaging and uploading to pypi

once the desired updates are made (inluding incrementing the version number in main and any other files touched by the update - te version update to main is critical as the pypi upload version is derrived from there and you cannot overwrite old version uploads), use hatch to build the new whl files. hatch uses the toml file to guide the whl build process
```python -m build```

now that the wheel files are made (they should have been deposited into the dist folder) you can upload them to pypi using twine
```python -m twine upload dist/*```
(you will need to paste your pypi API token as part of the upload)