; vmo studio installer
; NSIS 3.x / Unicode

Unicode true
RequestExecutionLevel admin

!include "MUI2.nsh"
!include "FileFunc.nsh"

!define APP_NAME "vmo studio"
!define APP_NAME_SAFE "VmoStudio"
!ifndef APP_VERSION
!define APP_VERSION "1.0.1"
!endif
!define APP_PUBLISHER "vmo studio"
!define APP_EXE "VmoStudio.exe"
!define APP_UNINST "Uninstall.exe"
!define APP_REGKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME_SAFE}"
!define APP_DIR_REGKEY "Software\${APP_NAME_SAFE}"
!define SOURCE_DIR "..\build\nuitka_vmostudio\run_desktop.dist"

Name "${APP_NAME}"
OutFile "..\dist\${APP_NAME_SAFE}_Setup_${APP_VERSION}.exe"
InstallDir "$PROGRAMFILES64\${APP_NAME}"
InstallDirRegKey HKCU "${APP_DIR_REGKEY}" ""

!define MUI_ABORTWARNING
!define MUI_ICON "..\desktop_assets\app.ico"
!define MUI_UNICON "..\desktop_assets\app.ico"
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "启动 ${APP_NAME}"

Var REMOVE_USER_DATA

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_WELCOME
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

Function .onInit
    ExecWait 'taskkill /F /IM ${APP_EXE}' $0
    Sleep 500
FunctionEnd

Function un.onInit
    MessageBox MB_OKCANCEL|MB_ICONINFORMATION \
        "确定要卸载 ${APP_NAME} 吗？$\n$\n卸载前会自动关闭正在运行的程序。" \
        IDOK continueUninstall IDCANCEL abortUninstall
    abortUninstall:
        Abort
    continueUninstall:

    ExecWait 'taskkill /F /IM ${APP_EXE}' $0
    Sleep 500

    MessageBox MB_YESNO|MB_ICONQUESTION \
        "是否同时删除本地数据？$\n$\n选择“否”会保留 data 和 output 目录，方便重新安装后继续使用。" \
        IDYES removeData IDNO keepData
    removeData:
        StrCpy $REMOVE_USER_DATA "1"
        Goto dataDecided
    keepData:
        StrCpy $REMOVE_USER_DATA "0"
    dataDecided:
FunctionEnd

Section "主程序" SecMain
    SectionIn RO

    SetOutPath "$INSTDIR"
    File /r "${SOURCE_DIR}\*.*"

    WriteUninstaller "$INSTDIR\${APP_UNINST}"

    ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
    IntFmt $0 "0x%08X" $0

    WriteRegStr HKCU "${APP_REGKEY}" "DisplayName" "${APP_NAME}"
    WriteRegStr HKCU "${APP_REGKEY}" "UninstallString" '"$INSTDIR\${APP_UNINST}"'
    WriteRegStr HKCU "${APP_REGKEY}" "QuietUninstallString" '"$INSTDIR\${APP_UNINST}" /S'
    WriteRegStr HKCU "${APP_REGKEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
    WriteRegStr HKCU "${APP_REGKEY}" "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKCU "${APP_REGKEY}" "Publisher" "${APP_PUBLISHER}"
    WriteRegStr HKCU "${APP_REGKEY}" "InstallLocation" "$INSTDIR"
    WriteRegDWORD HKCU "${APP_REGKEY}" "EstimatedSize" "$0"
    WriteRegDWORD HKCU "${APP_REGKEY}" "NoModify" 1
    WriteRegDWORD HKCU "${APP_REGKEY}" "NoRepair" 1
    WriteRegStr HKCU "${APP_DIR_REGKEY}" "InstallPath" "$INSTDIR"

    CreateDirectory "$SMPROGRAMS\${APP_NAME}"
    CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
    CreateShortcut "$SMPROGRAMS\${APP_NAME}\卸载 ${APP_NAME}.lnk" "$INSTDIR\${APP_UNINST}" "" "$INSTDIR\${APP_UNINST}" 0
SectionEnd

Section "桌面快捷方式" SecDesktop
    CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
SectionEnd

LangString DESC_SecMain ${LANG_SIMPCHINESE} "安装 vmo studio 主程序和运行时文件。"
LangString DESC_SecDesktop ${LANG_SIMPCHINESE} "在桌面创建快捷方式。"
LangString DESC_SecMain ${LANG_ENGLISH} "Install vmo studio application files and runtime."
LangString DESC_SecDesktop ${LANG_ENGLISH} "Create a desktop shortcut."

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
    !insertmacro MUI_DESCRIPTION_TEXT ${SecMain} $(DESC_SecMain)
    !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} $(DESC_SecDesktop)
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Section "Uninstall"
    Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
    Delete "$SMPROGRAMS\${APP_NAME}\卸载 ${APP_NAME}.lnk"
    RMDir "$SMPROGRAMS\${APP_NAME}"
    Delete "$DESKTOP\${APP_NAME}.lnk"

    StrCmp $REMOVE_USER_DATA "1" removeAll keepUserData
    removeAll:
        RMDir /r /REBOOTOK "$INSTDIR"
        Goto cleanupRegistry
    keepUserData:
        Delete /REBOOTOK "$INSTDIR\${APP_EXE}"
        Delete /REBOOTOK "$INSTDIR\${APP_UNINST}"
        Delete /REBOOTOK "$INSTDIR\*.dll"
        Delete /REBOOTOK "$INSTDIR\*.pyd"
        RMDir /r /REBOOTOK "$INSTDIR\static"
        RMDir /r /REBOOTOK "$INSTDIR\webview"
        RMDir /r /REBOOTOK "$INSTDIR\flask"
        RMDir /r /REBOOTOK "$INSTDIR\jinja2"
        RMDir /r /REBOOTOK "$INSTDIR\werkzeug"
        RMDir /r /REBOOTOK "$INSTDIR\requests"
        RMDir /r /REBOOTOK "$INSTDIR\curl_cffi"
        RMDir /r /REBOOTOK "$INSTDIR\cryptography"
        RMDir /r /REBOOTOK "$INSTDIR\certifi"
        RMDir /r /REBOOTOK "$INSTDIR\charset_normalizer"
        RMDir /r /REBOOTOK "$INSTDIR\PIL"
        RMDir /r /REBOOTOK "$INSTDIR\tzdata"
        RMDir /r /REBOOTOK "$INSTDIR\pythonnet"
        RMDir /r /REBOOTOK "$INSTDIR\clr_loader"

    cleanupRegistry:
        DeleteRegKey HKCU "${APP_REGKEY}"
        DeleteRegKey HKCU "${APP_DIR_REGKEY}"
SectionEnd
