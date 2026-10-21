// Source file for Vercel Web Analytics integration
// This file is bundled into analytics.js using: npm run build:analytics
import { inject } from '@vercel/analytics';

inject({ mode: 'production' });
