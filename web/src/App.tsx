import type { Session } from '@supabase/supabase-js'
import { useEffect, useState } from 'react'
import { Dashboard } from './components/Dashboard'
import { SignIn } from './components/SignIn'
import { isConfigured, supabase } from './lib/supabase'

export default function App() {
  // undefined until supabase-js has restored the session from storage
  const [session, setSession] = useState<Session | null>()

  useEffect(() => {
    if (!isConfigured) return
    const { data } = supabase.auth.onAuthStateChange((_event, session) => setSession(session))
    return () => data.subscription.unsubscribe()
  }, [])

  if (!isConfigured) {
    return (
      <main>
        <h1>cronchords</h1>
        <p>
          Build the dashboard with <code>VITE_SUPABASE_URL</code> and{' '}
          <code>VITE_SUPABASE_PUBLISHABLE_KEY</code> set. See the README.
        </p>
      </main>
    )
  }
  if (session === undefined) return null
  return session ? <Dashboard email={session.user.email} /> : <SignIn />
}
