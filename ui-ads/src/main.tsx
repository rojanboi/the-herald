import '@atlaskit/css-reset';
import './styles.css';

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import AppProvider from '@atlaskit/app-provider';

import { App } from './App';
import { HeraldProvider } from './store';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AppProvider defaultColorMode="light">
      <HeraldProvider>
        <App />
      </HeraldProvider>
    </AppProvider>
  </StrictMode>,
);
