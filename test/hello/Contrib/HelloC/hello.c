#include <windows.h>
#include <nsis/pluginapi.h>

HINSTANCE g_hInstance;

/* Hello::Greet <name> -> "Hello, <name>!" */
void __declspec(dllexport) Greet(HWND hwndParent, int string_size, LPTSTR variables, stack_t **stacktop, extra_parameters *extra)
{
  /* Kept under a 4 KiB page: bigger frames make MSVC call __chkstk, which crt: none doesn't link */
  TCHAR name[256], greeting[300];

  EXDLL_INIT();
  if (popstringn(name, sizeof(name) / sizeof(TCHAR)))
    name[0] = 0;
  wsprintf(greeting, TEXT("Hello, %s!"), name);
  pushstring(greeting);
}

/* Hello::Add <a> <b> -> a+b */
void __declspec(dllexport) Add(HWND hwndParent, int string_size, LPTSTR variables, stack_t **stacktop, extra_parameters *extra)
{
  INT_PTR a, b;

  EXDLL_INIT();
  a = popintptr();
  b = popintptr();
  pushintptr(a + b);
}

BOOL WINAPI DllMain(HINSTANCE hInst, DWORD reason, LPVOID reserved)
{
  g_hInstance = hInst;
  return TRUE;
}
