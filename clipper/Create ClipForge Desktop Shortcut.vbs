' Creates or repairs the ClipForge desktop shortcut.
Option Explicit
Dim fso, shell, appDir, launcher, dashboard, pythonw, desktop, shortcut, link, iconPath
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

appDir = fso.GetParentFolderName(WScript.ScriptFullName)
launcher = fso.BuildPath(appDir, "Launch ClipForge.vbs")
dashboard = fso.BuildPath(appDir, "dashboard.py")
pythonw = shell.ExpandEnvironmentStrings("%USERPROFILE%") & "\clipforge\.venv\Scripts\pythonw.exe"

If Not fso.FileExists(launcher) Or Not fso.FileExists(dashboard) Then
  MsgBox "ClipForge files are incomplete. Keep this installer, Launch ClipForge.vbs, and dashboard.py together in the clipper folder.", vbCritical, "ClipForge setup"
  WScript.Quit 1
End If

If Not fso.FileExists(pythonw) Then
  MsgBox "ClipForge's Python environment was not found:" & vbCrLf & pythonw & vbCrLf & vbCrLf & "Run the existing ClipForge setup first, then run this shortcut installer again.", vbExclamation, "ClipForge setup"
  WScript.Quit 1
End If

desktop = shell.SpecialFolders("Desktop")
link = fso.BuildPath(desktop, "ClipForge.lnk")
Set shortcut = shell.CreateShortcut(link)
shortcut.TargetPath = shell.ExpandEnvironmentStrings("%SystemRoot%\System32\wscript.exe")
shortcut.Arguments = """" & launcher & """"
shortcut.WorkingDirectory = appDir
shortcut.Description = "Open ClipForge Studio"
iconPath = shell.ExpandEnvironmentStrings("%USERPROFILE%") & "\clipforge\ClipForge.ico"
If fso.FileExists(iconPath) Then
  shortcut.IconLocation = iconPath & ", 0"
Else
  shortcut.IconLocation = shell.ExpandEnvironmentStrings("%SystemRoot%\System32\SHELL32.dll") & ", 167"
End If
shortcut.Save
MsgBox "ClipForge is ready on your desktop." & vbCrLf & "Double-click the ClipForge icon to open the app.", vbInformation, "ClipForge setup"
