import { initTheme } from '@/stores/themeStore';
import AntdProvider from '@/components/AntdProvider';

// Apply theme before React renders to avoid flash
initTheme();

import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import './styles/globals.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AntdProvider>
        <App />
      </AntdProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
