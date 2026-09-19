@echo off
chcp 65001 >nul
title 魔力局域网远程助手 · 主控端
cd /d %~dp0
python -m moli_remote client
