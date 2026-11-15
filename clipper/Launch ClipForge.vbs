' ClipForge desktop launcher. Double-click to open the native app without a console.
Option Explicit
Dim fso, shell, appDir, pythonw, dashboard, command
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = shell.ExpandEnvironmentStrings("%USERPROFILE%") & "\clipforge\.venv\Scripts\pythonw.exe"
dashboard = fso.BuildPath(appDir, "dashboard.py")

If Not fso.FileExists(dashboard) Then
  MsgBox "ClipForge could not find dashboard.py in:" & vbCrLf & appDir, vbCritical, "ClipForge"
  WScript.Quit 1
End If
If Not fso.FileExists(pythonw) Then
  MsgBox "ClipForge's Python environment was not found:" & vbCrLf & pythonw & vbCrLf & vbCrLf & "Run the ClipForge setup first.", vbExclamation, "ClipForge"
  WScript.Quit 1
End If

shell.CurrentDirectory = appDir
command = """" & pythonw & """ """ & dashboard & """"
shell.Run command, 0, False
