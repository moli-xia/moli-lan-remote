@echo off
chcp 65001 >nul
cd /d %~dp0..
echo [0/4] 清理残留进程(避免文件占用)...
taskkill /F /IM MoliHost.exe /T >nul 2>&1
taskkill /F /IM RomoterHost.exe /T >nul 2>&1
taskkill /F /IM MoliViewer.exe /T >nul 2>&1
taskkill /F /IM RomoterViewer.exe /T >nul 2>&1
echo [1/4] 生成图标...
python tools\make_icons.py || goto :err
echo [2/4] PyInstaller 打包(数分钟)...
python -m PyInstaller packaging\moli_remote.spec --noconfirm --distpath dist --workpath build || goto :err
echo [3/4] 查找 ISCC...
set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
echo [4/4] 生成安装包...
"%ISCC%" packaging\setup.iss || goto :err
echo.
echo 完成! 安装包: packaging\dist\MoliLanRemote-Setup-1.0.0.exe
exit /b 0
:err
echo 构建失败!
exit /b 1
