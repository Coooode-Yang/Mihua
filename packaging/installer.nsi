; Build with packaging/build_windows_exe.py; user data is never bundled or removed.
Unicode true
!ifndef VERSION
!define VERSION "0.5.7"
!endif
!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"
Name "米花"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\BaiAgent"
InstallDirRegKey HKCU "Software\BaiAgent" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 32
BrandingText "米花 · 学习与生活"
Icon "${ICON}"
UninstallIcon "${ICON}"
VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=2052 "ProductName" "米花"
VIAddVersionKey /LANG=2052 "FileDescription" "米花安装程序"
VIAddVersionKey /LANG=2052 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=2052 "LegalCopyright" "米花项目"
!define MUI_ABORTWARNING
!define MUI_ICON "${ICON}"
!define MUI_UNICON "${ICON}"
!define MUI_FINISHPAGE_RUN "$INSTDIR\米花.exe"
!define MUI_FINISHPAGE_RUN_TEXT "启动米花"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"
Var TestMode

; Test mode may only write below TEMP, never the registered installation.
!macro RequireTemporaryTestDirectory
    GetFullPathName $2 "$TEMP\"
    GetFullPathName $3 "$INSTDIR\"
    StrLen $4 $2
    StrCpy $5 $3 $4
    ${If} $5 != $2
    ${OrIf} $3 == $2
        SetErrorLevel 9
        Quit
    ${EndIf}
    ReadRegStr $6 HKCU "Software\BaiAgent" "InstallDir"
    ${If} $6 != ""
        GetFullPathName $6 "$6\"
        ${If} $3 == $6
            SetErrorLevel 9
            Quit
        ${EndIf}
    ${EndIf}
!macroend

Function .onInit
    SetShellVarContext current
    ${GetParameters} $0
    ClearErrors
    ${GetOptions} $0 "/WRITEUNINSTALLER" $1
    ${IfNot} ${Errors}
        SetOutPath "$INSTDIR"
        WriteUninstaller "$INSTDIR\卸载米花.exe"
        SetErrorLevel 0
        Quit
    ${EndIf}
    ${GetParameters} $0
    ClearErrors
    ${GetOptions} $0 "/TESTMODE" $1
    ${IfNot} ${Errors}
        StrCpy $TestMode "1"
        !insertmacro RequireTemporaryTestDirectory
    ${EndIf}
FunctionEnd

Section "米花" Main
    SetOutPath "$INSTDIR"
    SetOverwrite on
    !include "${FILES}"
    File /oname=米花.ico "${ICON}"
    ; The first installation gets defaults; upgrading preserves all existing data.
    SetOutPath "$INSTDIR\data"
    SetOverwrite off
    File "${PACKAGE}\data\settings.json"
    SetOverwrite on
    SetOutPath "$INSTDIR"
    WriteUninstaller "$INSTDIR\卸载米花.exe"
    ${If} $TestMode != "1"
        WriteRegStr HKCU "Software\BaiAgent" "InstallDir" "$INSTDIR"
        WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BaiAgent" "DisplayName" "米花"
        WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BaiAgent" "DisplayVersion" "${VERSION}"
        WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BaiAgent" "InstallLocation" "$INSTDIR"
        WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BaiAgent" "UninstallString" '$\"$INSTDIR\卸载米花.exe$\"'
        WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BaiAgent" "DisplayIcon" "$INSTDIR\米花.ico"
        WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BaiAgent" "NoModify" 1
        WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BaiAgent" "NoRepair" 1
        CreateShortcut "$DESKTOP\米花.lnk" "$INSTDIR\米花.exe" "" "$INSTDIR\米花.ico"
        CreateDirectory "$SMPROGRAMS\米花"
        CreateShortcut "$SMPROGRAMS\米花\米花.lnk" "$INSTDIR\米花.exe" "" "$INSTDIR\米花.ico"
        CreateShortcut "$SMPROGRAMS\米花\卸载米花.lnk" "$INSTDIR\卸载米花.exe"
    ${EndIf}
SectionEnd

Function un.onInit
    SetShellVarContext current
    ${GetParameters} $0
    ClearErrors
    ${GetOptions} $0 "/TESTMODE" $1
    ${IfNot} ${Errors}
        StrCpy $TestMode "1"
        !insertmacro RequireTemporaryTestDirectory
    ${EndIf}
FunctionEnd

Section "Uninstall"
    ; Only manifest-owned static files are deleted; data and unknown files stay.
    !include "${REMOVE_FILES}"
    Delete "$INSTDIR\米花.ico"
    Delete "$INSTDIR\卸载米花.exe"
    ${If} $TestMode != "1"
        Delete "$DESKTOP\米花.lnk"
        Delete "$SMPROGRAMS\米花\米花.lnk"
        Delete "$SMPROGRAMS\米花\卸载米花.lnk"
        RMDir "$SMPROGRAMS\米花"
        DeleteRegKey HKCU "Software\BaiAgent"
        DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BaiAgent"
    ${EndIf}
    RMDir "$INSTDIR"
SectionEnd
