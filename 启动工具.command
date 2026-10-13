#!/bin/zsh
cd "${0:A:h}" || exit 1
PYTHON_BIN="${SESX_PYTHON:-python3}"
if ! "$PYTHON_BIN" -c 'import tkinter' >/dev/null 2>&1; then
  echo '未找到带 Tkinter 的 Python。请参阅 使用说明.md，安装 python.org 的 macOS Python，或配置 SESX_PYTHON。'
  read '?按回车关闭'; exit 1
fi
"$PYTHON_BIN" app.py
if [ $? -ne 0 ]; then read '?启动失败，按回车关闭'; fi
