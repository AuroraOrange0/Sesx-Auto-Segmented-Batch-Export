"""Real Tk GUI; stub only the audio player, rendering remains real."""
import sys,time,json,hashlib
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_export import Tests
from app import App
from core import export_clip
import tkinter as tk
fixture=Tests(); fixture.setUp(); prefs=fixture.root/'settings.json'
root=tk.Tk(); app=App(root,prefs)
try:
    app.reload(fixture.sesx); root.update()
    app.vars['output'].set(str(fixture.out)); app.vars['mode'].set('峰值'); app.vars['peak'].set('-4')
    app.vars['channels'].set('左声道'); app.mapping['0']=str(fixture.source)
    app.directories['output']=str(fixture.out)
    # Debounced save must happen without closing or exporting.
    deadline=time.monotonic()+1
    while time.monotonic()<deadline: root.update(); time.sleep(.01)
    assert json.loads(prefs.read_text())['values']['peak']=='-4'
    app.close()
    root=tk.Tk(); app=App(root,prefs); root.update()
    assert app.session.path==fixture.sesx.resolve()
    assert app.vars['mode'].get()=='峰值' and app.vars['peak'].get()=='-4'
    assert app.mapping['0']==str(fixture.source)
    with patch('app.filedialog.askdirectory',return_value='') as dialog:
        app.pick_output(); assert dialog.call_args.kwargs['initialdir']==str(fixture.out)
    app.table.selection_set('0'); app.show_effects(); root.update()
    # Preview uses export_clip with the current options, not a separate approximation.
    with patch('app.run',return_value=('','')):
        app.preview_output()
        deadline=time.monotonic()+20
        while app.busy and time.monotonic()<deadline: root.update(); time.sleep(.02)
    assert not app.busy and app.last_preview_report
    preview=Path(app.last_preview_report['output'])
    result=export_clip(app.session,app.clips()[0],app.mapping,fixture.out,app.options(),set())
    assert hashlib.sha256(preview.read_bytes()).digest()==hashlib.sha256(Path(result['output']).read_bytes()).digest()
    assert app.last_preview_report['output_properties']['channels']==1
    assert app.log.winfo_y()+app.log.winfo_height()<=root.winfo_height()
    print('PASS: automatic save/restart restore, session/mapping/parameter restore, remembered picker directory, effect inspector, preview/export byte identity, GUI layout')
finally:
    app.close(); fixture.tearDown()
