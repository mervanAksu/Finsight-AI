// localStorage erişimi; gizli pencere veya engellenmiş depolamada sessizce başarısız olur.
export function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeStorage(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // depolama kullanılamıyor: tercih yalnızca bu oturumda geçerli kalır
  }
}
