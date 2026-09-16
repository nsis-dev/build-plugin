library HelloPascal;

uses
  nsis, SysUtils, Windows;

procedure Greet(const hwndParent: HWND; const string_size: integer; const variables: NSISPTChar; const stacktop: pointer); cdecl;
begin
  Init(hwndParent, string_size, variables, stacktop);
  PushString('Hello, ' + PopString() + '!');
end;

procedure Add(const hwndParent: HWND; const string_size: integer; const variables: NSISPTChar; const stacktop: pointer); cdecl;
var
  a, b: Integer;
begin
  Init(hwndParent, string_size, variables, stacktop);
  a := StrToInt(PopString());
  b := StrToInt(PopString());
  PushString(IntToStr(a + b));
end;

exports
  Greet, Add;

begin
end.
