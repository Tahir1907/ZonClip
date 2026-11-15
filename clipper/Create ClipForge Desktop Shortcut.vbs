' Creates a ClipForge shortcut on the Windows desktop.
Option Explicit
Dim fso, shell, appDir, launcher, desktop, shortcut, link
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
launcher = fso.BuildPath(appDir, "Launch ClipForge.vbs")
If Not fso.FileExists(launcher) Then
  MsgBox "Launch ClipForge.vbs must be in the same folder as this installer.", vbCritical, "ClipForge"
  WScript.Quit 1
End If

desktop = shell.SpecialFolders("Desktop")
link = fso.BuildPath(desktop, "ClipForge.lnk")
Set shortcut = shell.CreateShortcut(link)
shortcut.TargetPath = shell.ExpandEnvironmentStrings("%SystemRoot%\System32\wscript.exe")
shortcut.Arguments = """" & launcher & """"
shortcut.WorkingDirectory = appDir
shortcut.Description = "Open ClipForge Studio"
shortcut.IconLocation = shell.ExpandEnvironmentStrings("%SystemRoot%\System32\SHELL32.dll") & ", 167"
shortcut.Save
MsgBox "ClipForge shortcut created on your desktop." & vbCrLf & "Double-click ClipForge to open the app.", vbInformation, "ClipForge"
