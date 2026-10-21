import { useState, type FormEvent } from 'react'
import { supabase } from '../lib/supabase'
import { Status } from './Status'

export function SignIn() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    const { error } = await supabase.auth.signInWithPassword({ email, password })
    if (error) {
      setError(error.message)
      setBusy(false)
    }
  }

  return (
    <main className="page">
      <header className="top">
        <p className="wordmark">cronchords</p>
      </header>
      <div className="gate">
        <h1>Sign in</h1>
        <p>Use the user you created in your Supabase project under Authentication → Users.</p>
        <form onSubmit={submit}>
          <label>
            <span>Email</span>
            <input
              type="email"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </label>
          <label>
            <span>Password</span>
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          <button disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
          {error && (
            <Status tone="problem" role="alert">
              {error}
            </Status>
          )}
        </form>
      </div>
    </main>
  )
}
