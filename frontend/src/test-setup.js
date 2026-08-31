if(typeof window!=='undefined'){
 window.matchMedia??=query=>({matches:false,media:query,addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}});
 globalThis.ResizeObserver??=class{observe(){}unobserve(){}disconnect(){}};
 const getComputedStyle=window.getComputedStyle.bind(window);
 // jsdom does not implement pseudo-element styles. rc-util only probes the
 // scrollbar pseudo-element, so tests use the owning element's base style.
 window.getComputedStyle=element=>getComputedStyle(element);
}
