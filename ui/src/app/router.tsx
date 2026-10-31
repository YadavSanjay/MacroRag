import {
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
} from "@tanstack/react-router";
import { Shell } from "./Shell";
import { AskPage } from "@/features/business/ask/AskPage";
import { FactorsPage } from "@/features/business/factors/FactorsPage";
import { ScenarioPage } from "@/features/business/scenario/ScenarioPage";
import { CorpusPage } from "@/features/business/corpus/CorpusPage";
import { IndexPage } from "@/features/maintenance/index/IndexPage";
import { QualityPage } from "@/features/maintenance/quality/QualityPage";
import { IngestionPage } from "@/features/maintenance/ingestion/IngestionPage";
import { CurationPage } from "@/features/maintenance/curation/CurationPage";
import { HealthPage } from "@/features/system/health/HealthPage";
import { TracesPage } from "@/features/system/traces/TracesPage";
import { StoresPage } from "@/features/system/stores/StoresPage";
import { SystemMapPage } from "@/features/system/map/SystemMapPage";

const rootRoute = createRootRoute({ component: Shell });

const homeRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: () => {
    // `to` is cast because route paths aren't inferred yet at tree-definition time
    // (the registered router type depends on this very tree).
    throw redirect({ to: "/ask" as string });
  },
});

const route = (path: string, component: () => JSX.Element) =>
  createRoute({ getParentRoute: () => rootRoute, path, component });

const askRoute = route("/ask", AskPage);

// --- business ---
const factorsRoute = route("/factors", FactorsPage);
const scenarioRoute = route("/scenario", ScenarioPage);
const corpusRoute = route("/corpus", CorpusPage);

// --- internal maintenance (placeholders) ---
const ingestionRoute = route("/ingestion", IngestionPage);
const curationRoute = route("/curation", CurationPage);
const indexPageRoute = route("/index", IndexPage);
const qualityRoute = route("/quality", QualityPage);

// --- system / technical ---
const mapRoute = route("/system/map", SystemMapPage);
const tracesRoute = route("/system/traces", TracesPage);
const healthRoute = route("/system/health", HealthPage);
const storesRoute = route("/system/stores", StoresPage);

const routeTree = rootRoute.addChildren([
  homeRoute,
  askRoute,
  factorsRoute,
  scenarioRoute,
  corpusRoute,
  ingestionRoute,
  curationRoute,
  indexPageRoute,
  qualityRoute,
  mapRoute,
  tracesRoute,
  healthRoute,
  storesRoute,
]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
