$PSNativeCommandUseErrorActionPreference = $true
. .venv\Scripts\Activate.ps1
python -c "import sys; print(sys.executable)"
