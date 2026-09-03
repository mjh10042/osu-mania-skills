# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[('vendor/msd.exe', 'vendor')],
    datas=[('assets/skull.ico', 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='mania-skills',
    icon='assets/skull.ico',
    version='version.txt',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # Left off deliberately. It was never actually running - upx is not installed here, so
    # the old upx=True was a no-op and the build is byte-identical either way - but a
    # packed executable is one of the things antivirus ML models score against, and this
    # binary already draws a Wacatac.B!ml guess from Microsoft without any help.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
