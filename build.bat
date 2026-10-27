@echo off
echo ========================================================
echo  Compilando ODesk.exe (Single-file, No Console, Admin UAC)
echo ========================================================
taskkill /F /IM ODesk.exe >nul 2>&1
pyinstaller --noconsole --onefile --uac-admin --add-binary "screen_engine.dll;." --collect-all customtkinter --name "ODesk" odesk.py
if %ERRORLEVEL% EQU 0 (
    copy /Y dist\ODesk.exe .\ODesk.exe
    echo.
    echo [EXITO] ODesk.exe generado correctamente en la raiz del proyecto!
) else (
    echo.
    echo [ERROR] Fallo la compilacion.
)
