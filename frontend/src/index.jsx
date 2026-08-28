import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
export function mount(element) {
  const root = createRoot(element);
  root.render(<App />);
  return () => root.unmount();
}
