import { Link } from "@tanstack/react-router";
import { NAV } from "./nav";

export function NavRail() {
  return (
    <nav className="rail">
      <div className="rail-brand">MacroRAG</div>
      {NAV.map((group) => (
        <div className="rail-group" key={group.area}>
          <div className="rail-group-label">{group.label}</div>
          {group.items.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              className="rail-link"
              activeProps={{ className: "rail-link active" }}
            >
              <span className="glyph">{item.glyph}</span>
              <span>{item.label}</span>
              {!item.ready && (
                <span className="faint" style={{ marginLeft: "auto", fontSize: 10 }}>
                  soon
                </span>
              )}
            </Link>
          ))}
        </div>
      ))}
    </nav>
  );
}
