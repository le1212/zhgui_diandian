$ErrorActionPreference = 'Stop'
python -m unittest discover -s tests -v
pyinstaller --noconfirm --clean --onefile --windowed --name Diandian --icon assets\diandian.ico desktop_app.py
Write-Host "Built dist/Diandian.exe"
