; Smoke test: compiles only if <PLUGIN>.dll exports Greet and Add for this target.
; makensis -DTARGET=x86-unicode -DPLUGINS=<dir containing Plugins/> [-DPLUGIN=HelloRust] hello.nsi
!ifndef TARGET
  !error "define TARGET"
!endif
!ifndef PLUGINS
  !error "define PLUGINS"
!endif
!ifndef PLUGIN
  !define PLUGIN "Hello"
!endif

Target ${TARGET}
!addplugindir "${PLUGINS}/Plugins/${TARGET}"

Name "${PLUGIN}"
OutFile "hello-${TARGET}.exe"
RequestExecutionLevel user

Section
  ${PLUGIN}::Greet "NSIS"
  Pop $0
  DetailPrint $0

  ${PLUGIN}::Add 2 3
  Pop $0
  DetailPrint $0
SectionEnd
