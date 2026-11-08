/* Small native Windows entry point; keep the installed program's data location. */
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <wchar.h>
#include <stdlib.h>

static int exists(const wchar_t *path) {
    DWORD attributes = GetFileAttributesW(path);
    return attributes != INVALID_FILE_ATTRIBUTES && !(attributes & FILE_ATTRIBUTE_DIRECTORY);
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR arguments, int show) {
    (void)instance; (void)previous; (void)show;
    wchar_t folder[32768], target[32768], working[32768];
    DWORD length = GetModuleFileNameW(NULL, folder, 32768);
    if (!length || length >= 32768) return 2;
    wchar_t *separator = wcsrchr(folder, L'\\');
    if (!separator) return 2;
    *separator = 0;
    const wchar_t *candidates[] = {
        L"\\BaiAgent\\MihuaAgent.exe",
        L"\\MihuaAgent.exe",
        L"\\MihuaAgent\\MihuaAgent.exe"
    };
    int found = 0;
    for (size_t i = 0; i < sizeof(candidates)/sizeof(candidates[0]); ++i) {
        if (wcslen(folder) + wcslen(candidates[i]) >= 32768) continue;
        wcscpy(target, folder); wcscat(target, candidates[i]);
        if (exists(target)) { found = 1; break; }
    }
    if (!found) {
        MessageBoxW(NULL, L"找不到米花的程序文件。请从完整工程目录运行，或使用“米花安装程序.exe”安装后启动。",
                    L"米花", MB_OK | MB_ICONINFORMATION);
        return 2;
    }
    wcscpy(working, target); *wcsrchr(working, L'\\') = 0;
    size_t command_length = wcslen(target) + wcslen(arguments) + 5;
    if (command_length >= 32768) return 2;
    wchar_t *command = calloc(command_length, sizeof(wchar_t));
    if (!command) return 2;
    swprintf(command, command_length, L"\"%ls\" %ls", target, arguments);
    STARTUPINFOW startup = {0}; startup.cb = sizeof(startup);
    PROCESS_INFORMATION process = {0};
    SetDllDirectoryW(L"");
    BOOL created = CreateProcessW(target, command, NULL, NULL, FALSE, 0, NULL, working, &startup, &process);
    free(command);
    if (!created) {
        MessageBoxW(NULL, L"米花暂时未能启动，请检查程序文件是否完整，或重新运行安装程序。",
                    L"米花", MB_OK | MB_ICONERROR);
        return 3;
    }
    CloseHandle(process.hThread);
    DWORD code = 0;
    /* CLI self-checks wait for and return the real application's result. */
    if (*arguments) { WaitForSingleObject(process.hProcess, INFINITE); GetExitCodeProcess(process.hProcess, &code); }
    CloseHandle(process.hProcess);
    return (int)code;
}
