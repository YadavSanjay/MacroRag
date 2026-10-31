import { Outlet } from "@tanstack/react-router";
import { NavRail } from "./NavRail";
import { TopBar } from "./TopBar";

export function Shell() {
  return (
    <div className="app">
      <NavRail />
      <div className="main">
        <TopBar />
        <Outlet />
      </div>
    </div>
  );
}
