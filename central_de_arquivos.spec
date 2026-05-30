# -*- mode: python ; coding: utf-8 -*-
block_cipher = None

a = Analysis(
    ['renomear.py'],
    pathex=[],
    binaries=[
        ('gs/gswin64c.exe', 'gs'),
        ('gs/gsdll64.dll', 'gs'),
    ],
    datas=[],
    hiddenimports=['pypdf'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name='Central de Arquivos',
    debug=False, bootloader_ignore_signals=False, strip=False,
    upx=True, runtime_tmpdir=None, console=False,
)
