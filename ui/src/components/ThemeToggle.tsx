import { useTheme } from "@/theme/useTheme";

export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const next = theme === "light" ? "dark" : "light";
  return (
    <button
      className="btn"
      onClick={toggle}
      title={`Switch to ${next} theme`}
      aria-label={`Switch to ${next} theme`}
    >
      <span className="glyph" aria-hidden>
        {theme === "light" ? "◐" : "◑"}
      </span>
      {theme === "light" ? "Dark" : "Light"}
    </button>
  );
}
