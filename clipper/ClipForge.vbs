Option Explicit

Dim fso, shell, appDir, homeDir, pythonw, dashboard, shortcutPath, shortcut
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

appDir = fso.GetParentFolderName(WScript.ScriptFullName)
homeDir = shell.ExpandEnvironmentStrings("%CLIPFORGE_HOME%")
If homeDir = "%CLIPFORGE_HOME%" Or Len(Trim(homeDir)) = 0 Then
    homeDir = shell.ExpandEnvironmentStrings("%USERPROFILE%") & "\clipforge"
End If

pythonw = homeDir & "\.venv\Scripts\pythonw.exe"
dashboard = appDir & "\dashboard.py"

If Not fso.FileExists(pythonw) Then
    MsgBox "ClipForge's Python environment was not found:" & vbCrLf & pythonw & vbCrLf & vbCrLf & _
           "Run the one-time setup first, then double-click ClipForge.vbs again.", _
           vbExclamation, "ClipForge Studio"
    WScript.Quit 1
End If

If Not fso.FileExists(dashboard) Then
    MsgBox "The Studio launcher could not find dashboard.py in:" & vbCrLf & appDir, _
           vbCritical, "ClipForge Studio"
    WScript.Quit 1
End If

' Create a convenient desktop shortcut the first time this launcher is used.
shortcutPath = shell.SpecialFolders("Desktop") & "\ClipForge Studio.lnk"
On Error Resume Next
Set shortcut = shell.CreateShortcut(shortcutPath)
shortcut.TargetPath = WScript.ScriptFullName
shortcut.WorkingDirectory = appDir
shortcut.Description = "Open ClipForge Studio"
shortcut.IconLocation = shell.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\shell32.dll,220"
shortcut.Save
On Error GoTo 0

' pythonw.exe keeps the console hidden; dashboard.py opens the native WebView2 window.
shell.Environment("PROCESS")("PYTHONPATH") = appDir
shell.Run """" & pythonw & """ """ & dashboard & """", 0, False
