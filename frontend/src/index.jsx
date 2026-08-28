import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
export function mount(element) {
  const root = createRoot(element);
  root.render(<App />);
  return () => root.unmount();
}

import Portal from './Portal.jsx';
import {platformApi} from './platform-api.js';
export function mountPortal(element){
 const root=createRoot(element);root.render(<Portal api={platformApi}/>);return()=>root.unmount();
}
