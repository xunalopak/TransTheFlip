$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    python -m PyInstaller --noconfirm --onefile --windowed --name TransTheFlip-GUI --collect-all customtkinter --collect-all bleak --collect-all winrt --distpath ../dist --workpath ../build/gui --specpath ../build trans_gui.py
    if ($LASTEXITCODE -ne 0) { throw 'GUI build failed' }
    python -m PyInstaller --noconfirm --onefile --console --name TransTheFlip-CLI --collect-all bleak --collect-all winrt --distpath ../dist --workpath ../build/cli --specpath ../build trans_client.py
    if ($LASTEXITCODE -ne 0) { throw 'CLI build failed' }
} finally {
    Pop-Location
}
