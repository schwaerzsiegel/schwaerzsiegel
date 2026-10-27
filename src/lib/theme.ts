// Phase 4: Hell-/Dunkelmodus. "system" folgt Windows. Die Wahl ist eine
// reine Komfort-Einstellung pro Gerät (localStorage, darf fehlen).

export type ThemeChoice = "system" | "light" | "dark";

const KEY = "redaction-theme";

export function loadTheme(): ThemeChoice {
  try {
    const value = localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function saveTheme(choice: ThemeChoice) {
  try {
    localStorage.setItem(KEY, choice);
  } catch {
    // ohne Speicher gilt die Wahl nur bis zum Schließen
  }
}

export function applyTheme(choice: ThemeChoice) {
  document.documentElement.dataset.theme = choice;
}

export function nextTheme(choice: ThemeChoice): ThemeChoice {
  return choice === "system" ? "light" : choice === "light" ? "dark" : "system";
}

export const THEME_LABEL: Record<ThemeChoice, string> = {
  system: "Darstellung wie Windows",
  light: "Helle Darstellung",
  dark: "Dunkle Darstellung",
};
