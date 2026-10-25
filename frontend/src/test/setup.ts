import { Storage } from 'happy-dom';

// Node's experimental Web Storage can shadow happy-dom's Storage globals.
// Use happy-dom's browser implementation rather than Node's experimental API.
Object.defineProperty(globalThis, 'localStorage', {
  configurable: true,
  value: new Storage(),
});
Object.defineProperty(globalThis, 'sessionStorage', {
  configurable: true,
  value: new Storage(),
});
