"""Desktop smoke test; requires a real macOS window session."""
import sys, time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_export import Tests
from app import App
import tkinter as tk
fixture=Tests(); fixture.setUp()
root=tk.Tk(); app=App(root,fixture.root/"gui-settings.json")
try:
    app.reload(str(fixture.sesx)); root.update()
    assert len(app.table.get_children())==2
    assert app.track.get()=='1: 有效稿'
    app.vars['output'].set(str(fixture.out)); app.start(False)
    deadline=time.monotonic()+30
    while app.busy and time.monotonic()<deadline:
        root.update(); time.sleep(.02)
    assert not app.busy, 'GUI export timed out'
    assert len(list(fixture.out.glob('*.wav')))==2
    assert len(list(fixture.out.glob('*.jsonl')))==1
    assert all(app.table.set(i,'status')=='完成' for i in app.table.get_children())
    assert app.log.winfo_y()+app.log.winfo_height()<=root.winfo_height()
    print('PASS: macOS Tk window, SESX load, track/preview, GUI worker export, duplicate numbering, status/log, window layout')
finally:
    root.destroy(); fixture.tearDown()
