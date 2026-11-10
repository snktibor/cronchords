import '@fontsource-variable/instrument-sans'
import { inject } from '@vercel/analytics'
import './style.css'

// Vercel Web Analytics: cookieless page views, for this site only. The
// dashboard in web/ is every user's own copy and never gets analytics.
inject()
