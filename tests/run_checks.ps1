$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    python -m unittest discover -s pc_client
    if ($LASTEXITCODE -ne 0) { throw 'Python checks failed' }
    New-Item -ItemType Directory -Force build/tests | Out-Null
    gcc -std=c11 -Wall -Wextra -Werror tests/test_protocol.c -o build/tests/test_protocol.exe
    if ($LASTEXITCODE -ne 0) { throw 'Protocol test build failed' }
    & ./build/tests/test_protocol.exe
    if ($LASTEXITCODE -ne 0) { throw 'Protocol checks failed' }
    gcc -std=c11 -Wall -Wextra -Werror -Itests/stubs tests/test_hid.c trans_the_flip_hid.c -o build/tests/test_hid.exe
    if ($LASTEXITCODE -ne 0) { throw 'HID test build failed' }
    & ./build/tests/test_hid.exe
    if ($LASTEXITCODE -ne 0) { throw 'HID checks failed' }
} finally {
    Pop-Location
}
