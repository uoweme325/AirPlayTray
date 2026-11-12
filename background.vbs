Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
python = dir & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(python) Then
    MsgBox "Run install.cmd first.", 48, "AirPlayTray"
ElseIf Not fso.FileExists(dir & "\config.json") Then
    MsgBox "Copy config.example.json to config.json and select your speaker first.", 48, "AirPlayTray"
Else
    sh.CurrentDirectory = dir
    sh.Run """" & python & """ """ & dir & "\tray_app.py""", 0, False
End If
