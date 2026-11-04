import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { WorkforceChart } from "./WorkforceChart.tsx";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("Missing application root");
createRoot(root).render(<StrictMode><WorkforceChart /></StrictMode>);
