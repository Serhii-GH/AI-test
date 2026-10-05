import { useCallback, useEffect, useMemo, useState } from 'react'
import './App.css'

const currencyFormatter = new Intl.NumberFormat('uk-UA', {
  style: 'currency',
  currency: 'UAH',
  maximumFractionDigits: 2,
})

const usdFormatter = new Intl.NumberFormat('uk-UA', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 2,
})

const dateFormatter = new Intl.DateTimeFormat('uk-UA', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
})

const barColors = ['#c65b3f', '#d5864c', '#8d9a70', '#556b63', '#b6a176', '#7d6656']
const TRANSACTION_PAGE_SIZE = 10

function getTodayForInput() {
  const now = new Date()
  const timezoneOffset = now.getTimezoneOffset() * 60_000
  return new Date(now.getTime() - timezoneOffset).toISOString().slice(0, 10)
}

function createEmptyTransactionForm() {
  return {
    type: 'expense',
    amount: '',
    exchange_rate: '',
    category: 'Роботи',
    subcategory: '',
    description: '',
    date: getTodayForInput(),
  }
}

function formatCurrency(value) {
  return currencyFormatter.format(Number(value ?? 0))
}

function formatUsd(value) {
  return usdFormatter.format(Number(value ?? 0))
}

function formatExchangeRate(value) {
  return Number(value ?? 0).toFixed(2)
}

function formatDate(value) {
  return value ? dateFormatter.format(new Date(value)) : '—'
}

function sortTotals(totals) {
  return [...totals.entries()]
    .map(([label, amount]) => ({ label, amount }))
    .sort((left, right) => right.amount - left.amount || left.label.localeCompare(right.label, 'uk'))
}

function getSubcategoryTotals(transactions, mainCategory) {
  const totals = new Map()
  transactions
    .filter((transaction) => transaction.transaction_type === 'expense' && transaction.main_category === mainCategory)
    .forEach((transaction) => {
      const label = transaction.subcategory || 'Без підкатегорії'
      totals.set(label, (totals.get(label) ?? 0) + Number(transaction.amount))
    })
  return sortTotals(totals)
}

function ExpenseDistributionChart({ title, description, totals, isLoading, emptyMessage }) {
  const largestTotal = Math.max(...totals.map((item) => item.amount), 0)

  return (
    <article className="distribution-card">
      <div className="distribution-card-heading">
        <div>
          <p className="panel-kicker">{description}</p>
          <h3>{title}</h3>
        </div>
        <span className="distribution-count">{totals.length}</span>
      </div>
      {isLoading ? (
        <div className="distribution-placeholder">Завантажуємо діаграму…</div>
      ) : totals.length > 0 ? (
        <div className="distribution-bars" aria-label={title}>
          {totals.map((item, index) => (
            <div className="distribution-row" key={item.label}>
              <div className="distribution-label-row">
                <span>{item.label}</span>
                <strong>{formatCurrency(item.amount)}</strong>
              </div>
              <div className="distribution-track">
                <div
                  className="distribution-fill"
                  style={{
                    width: `${Math.max((item.amount / largestTotal) * 100, 5)}%`,
                    '--bar-color': barColors[index % barColors.length],
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      ) : <div className="distribution-placeholder">{emptyMessage}</div>}
    </article>
  )
}

function consumeSseEvents(buffer, onEvent) {
  let remainder = buffer
  let boundary = remainder.search(/\r?\n\r?\n/)

  while (boundary !== -1) {
    const block = remainder.slice(0, boundary)
    remainder = remainder.slice(boundary).replace(/^\r?\n\r?\n/, '')
    const event = block.match(/^event:\s*(.+)$/m)?.[1]?.trim()
    const data = block
      .split(/\r?\n/)
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trimStart())
      .join('\n')
    if (event && data) {
      try {
        onEvent(event, JSON.parse(data))
      } catch {
        // A malformed event is ignored; the following error/done event remains usable.
      }
    }
    boundary = remainder.search(/\r?\n\r?\n/)
  }

  return remainder
}

function GuestDashboard({ session, onLogout }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    fetch('/api/guest/dashboard')
      .then(async (response) => {
        if (!response.ok) throw new Error('Не вдалося завантажити дані огляду.')
        return response.json()
      })
      .then(setData)
      .catch((requestError) => setError(requestError.message))
  }, [])

  return (
    <main className="dashboard-shell">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">Гостьовий перегляд</p>
          <h1>{session.project_name}</h1>
          <p className="subtitle">Доступ лише для перегляду. Редагування та видалення операцій недоступні.</p>
        </div>
        <button className="logout-button" type="button" onClick={onLogout}>Вийти</button>
      </header>
      {error && <section className="message-card error-card"><p>{error}</p></section>}
      {!data && !error && <section className="message-card"><p>Завантажуємо дані…</p></section>}
      {data && <>
        <section className="summary-grid">
          <article className="summary-card income-card"><span className="card-label">Загальний дохід</span><strong>{formatCurrency(data.summary.total_income)}</strong></article>
          <article className="summary-card expense-card"><span className="card-label">Загальні витрати</span><strong>{formatCurrency(data.summary.total_expense)}</strong></article>
          <article className="summary-card balance-card"><span className="card-label">Поточний баланс</span><strong>{formatCurrency(data.summary.balance)}</strong></article>
        </section>
        <section className="panel transaction-list"><h2>Операції</h2>{data.transactions.map((item) => <div className="transaction-row" key={item.id}><span>{formatDate(item.created_at)} · {item.main_category} · {item.subcategory}<small>{item.description}</small></span><strong>{formatCurrency(item.amount)}</strong></div>)}</section>
      </>}
    </main>
  )
}

function App() {
  const [telegramId, setTelegramId] = useState(null)
  const [telegramUsername, setTelegramUsername] = useState(null)
  const [telegramAvatarUrl, setTelegramAvatarUrl] = useState(null)
  const [authStatus, setAuthStatus] = useState('checking')
  const [authError, setAuthError] = useState('')
  const [telegramLogin, setTelegramLogin] = useState(null)
  const [loginAttempt, setLoginAttempt] = useState(0)
  const [guestSession, setGuestSession] = useState(null)
  const [showGuestLogin, setShowGuestLogin] = useState(false)
  const [guestLogin, setGuestLogin] = useState('')
  const [guestPassword, setGuestPassword] = useState('')
  const [guestError, setGuestError] = useState('')
  const [guestCredential, setGuestCredential] = useState(null)
  const [guestAccesses, setGuestAccesses] = useState([])
  const [guestAccessError, setGuestAccessError] = useState('')
  const [summary, setSummary] = useState(null)
  const [transactions, setTransactions] = useState([])
  const [projects, setProjects] = useState([])
  const [activeProjectId, setActiveProjectId] = useState(null)
  const [newProjectName, setNewProjectName] = useState('')
  const [projectError, setProjectError] = useState('')
  const [isCreatingProject, setIsCreatingProject] = useState(false)
  const [isExporting, setIsExporting] = useState(false)
  const [exportError, setExportError] = useState('')
  const [status, setStatus] = useState('idle')
  const [error, setError] = useState('')
  const [transactionForm, setTransactionForm] = useState(createEmptyTransactionForm)
  const [transactionError, setTransactionError] = useState('')
  const [isSubmittingTransaction, setIsSubmittingTransaction] = useState(false)
  const [transactionActionError, setTransactionActionError] = useState('')
  const [deletingTransactionId, setDeletingTransactionId] = useState(null)
  const [transactionFilter, setTransactionFilter] = useState('all')
  const [visibleTransactionLimit, setVisibleTransactionLimit] = useState(TRANSACTION_PAGE_SIZE)
  const [analysis, setAnalysis] = useState(null)
  const [analysisStatus, setAnalysisStatus] = useState('idle')
  const [analysisError, setAnalysisError] = useState('')
  const [chatThreads, setChatThreads] = useState([])
  const [selectedChatThreadId, setSelectedChatThreadId] = useState(null)
  const [chatMessages, setChatMessages] = useState([])
  const [chatInput, setChatInput] = useState('')
  const [chatStatus, setChatStatus] = useState('idle')
  const [chatError, setChatError] = useState('')
  const [pendingActions, setPendingActions] = useState([])
  const [resolvingActionId, setResolvingActionId] = useState(null)

  const loadProjects = useCallback(async () => {
    if (!telegramId) {
      return
    }
    try {
      const response = await fetch('/api/projects')
      if (!response.ok) {
        throw new Error('Не вдалося завантажити проєкти.')
      }
      const projectData = await response.json()
      setProjects(projectData)
      setActiveProjectId((currentProjectId) => (
        projectData.some((project) => project.id === currentProjectId)
          ? currentProjectId
          : projectData[0]?.id ?? null
      ))
    } catch (requestError) {
      setProjectError(requestError.message)
    }
  }, [telegramId])

  const loadDashboard = useCallback(async (showLoading = true) => {
    if (!telegramId || !activeProjectId) {
      return
    }

    if (showLoading) {
      setStatus('loading')
      setError('')
    }

    try {
      const [summaryResponse, transactionsResponse] = await Promise.all([
        fetch(`/api/summary?project_id=${activeProjectId}`),
        fetch(`/api/transactions?project_id=${activeProjectId}`),
      ])

      if (!summaryResponse.ok || !transactionsResponse.ok) {
        throw new Error('Не вдалося завантажити фінансові дані.')
      }

      const [summaryData, transactionsData] = await Promise.all([
        summaryResponse.json(),
        transactionsResponse.json(),
      ])

      setSummary(summaryData)
      setTransactions(transactionsData)
      setStatus('ready')
    } catch (requestError) {
      setStatus('error')
      setError(requestError.message)
    }
  }, [activeProjectId, telegramId])

  const loadChatThreads = useCallback(async () => {
    if (!telegramId || !activeProjectId) {
      return
    }
    try {
      const response = await fetch(`/api/ai/chat/threads?project_id=${activeProjectId}`)
      if (!response.ok) {
        throw new Error('Не вдалося завантажити попередні діалоги.')
      }
      setChatThreads(await response.json())
    } catch (requestError) {
      setChatError(requestError.message || 'Не вдалося завантажити попередні діалоги.')
    }
  }, [activeProjectId, telegramId])

  useEffect(() => {
    let isCurrent = true

    async function loadSession() {
      try {
        const response = await fetch('/api/auth/session')
        if (!response.ok) {
          throw new Error('No active session')
        }
        const session = await response.json()
        if (isCurrent) {
          setTelegramId(String(session.telegram_id))
          setTelegramUsername(session.username ?? null)
          setAuthStatus('authenticated')
        }
      } catch {
        try {
          const guestResponse = await fetch('/api/guest/session')
          if (!guestResponse.ok) throw new Error('No guest session')
          const guest = await guestResponse.json()
          if (isCurrent) {
            setGuestSession(guest)
            setAuthStatus('guest')
          }
        } catch {
          if (isCurrent) setAuthStatus('unauthenticated')
        }
      }
    }

    void loadSession()
    return () => { isCurrent = false }
  }, [])

  useEffect(() => {
    if (authStatus !== 'unauthenticated') {
      return undefined
    }

    let isCurrent = true
    let pollTimer = null

    async function startAndPollTelegramLogin() {
      setTelegramLogin(null)
      setAuthError('')
      try {
        const response = await fetch('/api/auth/challenges', { method: 'POST' })
        if (!response.ok) {
          const body = await response.json().catch(() => null)
          throw new Error(typeof body?.detail === 'string' ? body.detail : 'Не вдалося підготувати вхід через Telegram.')
        }

        const login = await response.json()
        if (!isCurrent) {
          return
        }
        setTelegramLogin(login)

        async function pollChallenge() {
          try {
            const pollResponse = await fetch(`/api/auth/challenges/${encodeURIComponent(login.challenge)}`)
            if (pollResponse.status === 410) {
              throw new Error('Посилання для входу прострочене. Створіть нове.')
            }
            if (!pollResponse.ok) {
              throw new Error('Не вдалося перевірити підтвердження входу.')
            }
            const status = await pollResponse.json()
            if (!isCurrent) {
              return
            }
            if (status.status === 'authenticated') {
              setTelegramId(String(status.telegram_id))
              setTelegramUsername(status.username ?? null)
              setAuthStatus('authenticated')
              return
            }
            pollTimer = window.setTimeout(() => { void pollChallenge() }, 2000)
          } catch (requestError) {
            if (isCurrent) {
              setTelegramLogin(null)
              setAuthError(requestError.message || 'Не вдалося завершити вхід через Telegram.')
            }
          }
        }

        void pollChallenge()
      } catch (requestError) {
        if (isCurrent) {
          setAuthError(requestError.message || 'Не вдалося підготувати вхід через Telegram.')
        }
      }
    }

    void startAndPollTelegramLogin()
    return () => {
      isCurrent = false
      if (pollTimer !== null) {
        window.clearTimeout(pollTimer)
      }
    }
  }, [authStatus, loginAttempt])

  async function submitGuestLogin(event) {
    event.preventDefault()
    setGuestError('')
    try {
      const response = await fetch('/api/guest/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ login: guestLogin, password: guestPassword }) })
      if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'Не вдалося увійти.')
      setGuestSession(await response.json())
      setAuthStatus('guest')
    } catch (requestError) {
      setGuestError(requestError.message)
    }
  }

  async function logoutGuest() {
    await fetch('/api/guest/logout', { method: 'POST' })
    setGuestSession(null)
    setGuestLogin('')
    setGuestPassword('')
    setAuthStatus('unauthenticated')
  }

  async function createGuestAccess() {
    if (!activeProjectId) return
    setGuestAccessError('')
    try {
      const response = await fetch(`/api/projects/${activeProjectId}/guest-accesses`, { method: 'POST' })
      if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'Не вдалося створити доступ.')
      const credential = await response.json()
      setGuestCredential(credential)
      setGuestAccesses((current) => [credential, ...current])
    } catch (requestError) {
      setGuestAccessError(requestError.message)
    }
  }

  async function revokeGuestAccess() {
    if (!guestCredential || !activeProjectId) return
    const response = await fetch(`/api/projects/${activeProjectId}/guest-accesses/${guestCredential.id}`, { method: 'DELETE' })
    if (response.ok) {
      setGuestAccesses((current) => current.filter((access) => access.id !== guestCredential.id))
      setGuestCredential(null)
    }
    else setGuestAccessError('Не вдалося відкликати доступ.')
  }

  useEffect(() => {
    let avatarUrl = null
    let isCurrent = true

    async function loadTelegramAvatar() {
      if (!telegramId) {
        setTelegramAvatarUrl(null)
        return
      }

      try {
        const response = await fetch('/api/auth/avatar')
        if (response.status !== 200 || !response.headers.get('content-type')?.startsWith('image/')) {
          return
        }
        avatarUrl = URL.createObjectURL(await response.blob())
        if (isCurrent) {
          setTelegramAvatarUrl(avatarUrl)
        }
      } catch {
        if (isCurrent) {
          setTelegramAvatarUrl(null)
        }
      }
    }

    void loadTelegramAvatar()
    return () => {
      isCurrent = false
      if (avatarUrl) {
        URL.revokeObjectURL(avatarUrl)
      }
    }
  }, [telegramId])

  useEffect(() => {
    if (!telegramId) {
      return undefined
    }

    const loadTimer = window.setTimeout(() => { void loadProjects() }, 0)
    return () => window.clearTimeout(loadTimer)
  }, [loadProjects, telegramId])

  useEffect(() => {
    if (!telegramId || !activeProjectId) {
      return undefined
    }

    const loadTimer = window.setTimeout(() => { void loadDashboard(false) }, 0)
    return () => window.clearTimeout(loadTimer)
  }, [activeProjectId, loadDashboard, telegramId])

  useEffect(() => {
    if (!telegramId || !activeProjectId) {
      return undefined
    }

    setSelectedChatThreadId(null)
    setChatMessages([])
    setPendingActions([])
    setChatError('')
    const loadTimer = window.setTimeout(() => { void loadChatThreads() }, 0)
    return () => window.clearTimeout(loadTimer)
  }, [activeProjectId, loadChatThreads, telegramId])

  useEffect(() => {
    if (!telegramId || !activeProjectId) return undefined
    let current = true
    fetch(`/api/projects/${activeProjectId}/guest-accesses`)
      .then((response) => response.ok ? response.json() : [])
      .then((accesses) => { if (current) setGuestAccesses(accesses) })
    return () => { current = false }
  }, [activeProjectId, telegramId])

  async function logout() {
    await fetch('/api/auth/logout', { method: 'POST' })
    setTelegramId(null)
    setTelegramUsername(null)
    setTelegramAvatarUrl(null)
    setSummary(null)
    setTransactions([])
    setProjects([])
    setActiveProjectId(null)
    setAnalysis(null)
    setAnalysisStatus('idle')
    setChatThreads([])
    setSelectedChatThreadId(null)
    setChatMessages([])
    setChatInput('')
    setChatStatus('idle')
    setChatError('')
    setPendingActions([])
    setResolvingActionId(null)
    setStatus('idle')
    setAuthStatus('unauthenticated')
  }

  function updateTransactionForm(event) {
    const { name, value } = event.target
    setTransactionForm((currentForm) => ({ ...currentForm, [name]: value }))
  }

  async function createProject(event) {
    event.preventDefault()
    const name = newProjectName.trim()
    if (!name) {
      setProjectError('Вкажіть назву нового фінансового огляду.')
      return
    }

    setIsCreatingProject(true)
    setProjectError('')
    try {
      const response = await fetch('/api/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })
      if (!response.ok) {
        const responseBody = await response.json().catch(() => null)
        throw new Error(typeof responseBody?.detail === 'string' ? responseBody.detail : 'Не вдалося створити проєкт.')
      }
      const project = await response.json()
      setProjects((currentProjects) => [...currentProjects, project])
      setNewProjectName('')
      setActiveProjectId(project.id)
      setAnalysis(null)
      setAnalysisStatus('idle')
      setVisibleTransactionLimit(TRANSACTION_PAGE_SIZE)
    } catch (requestError) {
      setProjectError(requestError.message || 'Не вдалося створити проєкт. Спробуйте ще раз.')
    } finally {
      setIsCreatingProject(false)
    }
  }

  async function exportTransactions() {
    if (!activeProjectId) {
      return
    }

    setIsExporting(true)
    setExportError('')
    try {
      const response = await fetch(`/api/transactions/export?project_id=${activeProjectId}`)
      if (!response.ok) {
        const body = await response.json().catch(() => null)
        throw new Error(typeof body?.detail === 'string' ? body.detail : 'Не вдалося експортувати операції.')
      }

      const blob = await response.blob()
      const downloadUrl = URL.createObjectURL(blob)
      const link = document.createElement('a')
      const filename = response.headers.get('content-disposition')?.match(/filename="?([^";]+)"?/)?.[1]
      link.href = downloadUrl
      link.download = filename || 'finance-export.csv'
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(downloadUrl)
    } catch (requestError) {
      setExportError(requestError.message || 'Не вдалося експортувати операції. Спробуйте ще раз.')
    } finally {
      setIsExporting(false)
    }
  }

  async function submitTransaction(event) {
    event.preventDefault()
    if (!telegramId) {
      setTransactionError('Спочатку підключіть Telegram ID, щоб зберігати операції у своєму обліку.')
      return
    }

    const amount = transactionForm.amount.trim()
    const exchangeRate = transactionForm.exchange_rate.trim()
    const category = transactionForm.category.trim()
    const subcategory = transactionForm.subcategory.trim()
    const parsedAmount = Number(amount)
    const parsedExchangeRate = Number(exchangeRate)

    if (!amount) {
      setTransactionError('Вкажіть суму операції.')
      return
    }
    if (!Number.isFinite(parsedAmount)) {
      setTransactionError('Сума має бути числом.')
      return
    }
    if (parsedAmount <= 0) {
      setTransactionError('Сума має бути більшою за нуль.')
      return
    }
    if (!['income', 'expense'].includes(transactionForm.type)) {
      setTransactionError('Оберіть коректний тип операції.')
      return
    }
    if (!['Роботи', 'Матеріали'].includes(category)) {
      setTransactionError('Оберіть категорію «Роботи» або «Матеріали».')
      return
    }
    if (!exchangeRate || !Number.isFinite(parsedExchangeRate) || parsedExchangeRate <= 0) {
      setTransactionError('Вкажіть додатний курс USD у гривнях на дату операції.')
      return
    }
    if (!subcategory) {
      setTransactionError('Вкажіть підкатегорію операції.')
      return
    }

    setIsSubmittingTransaction(true)
    setTransactionError('')

    try {
      const response = await fetch('/api/transactions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...transactionForm,
          project_id: activeProjectId,
          amount,
          exchange_rate: exchangeRate,
          category,
          subcategory,
        }),
      })

      if (!response.ok) {
        const responseBody = await response.json().catch(() => null)
        const detail = responseBody?.detail
        const message = typeof detail === 'string'
          ? detail
          : 'Не вдалося зберегти операцію. Перевірте заповнені поля та спробуйте ще раз.'
        throw new Error(message)
      }

      setTransactionForm(createEmptyTransactionForm())
      setAnalysis(null)
      await loadDashboard()
    } catch (requestError) {
      setTransactionError(requestError.message || 'Не вдалося зберегти операцію. Спробуйте ще раз.')
    } finally {
      setIsSubmittingTransaction(false)
    }
  }

  async function deleteTransaction(transactionId) {
    if (!telegramId || !activeProjectId || !window.confirm('Видалити операцію?')) {
      return
    }

    setDeletingTransactionId(transactionId)
    setTransactionActionError('')

    try {
      const response = await fetch(`/api/transactions/${transactionId}?project_id=${activeProjectId}`, { method: 'DELETE' })

      if (!response.ok) {
        const responseBody = await response.json().catch(() => null)
        throw new Error(
          typeof responseBody?.detail === 'string'
            ? responseBody.detail
            : 'Не вдалося видалити операцію. Спробуйте ще раз.',
        )
      }

      await loadDashboard()
      setAnalysis(null)
    } catch (requestError) {
      setTransactionActionError(requestError.message || 'Не вдалося видалити операцію. Спробуйте ще раз.')
    } finally {
      setDeletingTransactionId(null)
    }
  }

  async function runAiAnalysis() {
    if (!telegramId || !activeProjectId) {
      return
    }

    setAnalysisStatus('loading')
    setAnalysisError('')
    try {
      const response = await fetch(`/api/ai/analyze-transactions?project_id=${activeProjectId}`, { method: 'POST' })
      if (!response.ok) {
        const responseBody = await response.json().catch(() => null)
        throw new Error(
          typeof responseBody?.detail === 'string'
            ? responseBody.detail
            : 'Не вдалося виконати AI-аналіз. Спробуйте ще раз.',
        )
      }
      setAnalysis(await response.json())
      setAnalysisStatus('ready')
    } catch (requestError) {
      setAnalysisError(requestError.message || 'Не вдалося виконати AI-аналіз. Спробуйте ще раз.')
      setAnalysisStatus('error')
    }
  }

  function startNewChat() {
    if (chatStatus === 'sending') {
      return
    }
    setSelectedChatThreadId(null)
    setChatMessages([])
    setPendingActions([])
    setChatInput('')
    setChatError('')
  }

  async function selectChatThread(threadId) {
    if (!activeProjectId || chatStatus === 'sending') {
      return
    }

    setChatStatus('loading')
    setChatError('')
    try {
      const response = await fetch(`/api/ai/chat/threads/${threadId}?project_id=${activeProjectId}`)
      if (!response.ok) {
        const body = await response.json().catch(() => null)
        throw new Error(typeof body?.detail === 'string' ? body.detail : 'Не вдалося відкрити діалог.')
      }
      const thread = await response.json()
      setSelectedChatThreadId(thread.id)
      setChatMessages(thread.messages.map((message, index) => ({ ...message, id: `${thread.id}-${index}` })))
      setPendingActions(thread.pending_actions ?? [])
    } catch (requestError) {
      setChatError(requestError.message || 'Не вдалося відкрити діалог.')
    } finally {
      setChatStatus('idle')
    }
  }

  async function sendChatMessage(event) {
    event.preventDefault()
    const message = chatInput.trim()
    if (!message || !activeProjectId || chatStatus === 'sending') {
      return
    }

    const pendingId = `assistant-${Date.now()}`
    setChatStatus('sending')
    setChatError('')
    setChatInput('')

    try {
      const response = await fetch('/api/ai/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify({
          message,
          project_id: activeProjectId,
          thread_id: selectedChatThreadId,
        }),
      })
      if (!response.ok || !response.body) {
        const body = await response.json().catch(() => null)
        throw new Error(typeof body?.detail === 'string' ? body.detail : 'Не вдалося надіслати повідомлення.')
      }

      setChatMessages((currentMessages) => [
        ...currentMessages,
        { id: `user-${Date.now()}`, role: 'user', content: message },
        { id: pendingId, role: 'assistant', content: '' },
      ])

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let streamError = null
      const handleEvent = (eventName, payload) => {
        if (eventName === 'thread' && payload.thread_id) {
          setSelectedChatThreadId(payload.thread_id)
        }
        if (eventName === 'delta' && typeof payload.text === 'string') {
          setChatMessages((currentMessages) => currentMessages.map((chatMessage) => (
            chatMessage.id === pendingId
              ? { ...chatMessage, content: `${chatMessage.content}${payload.text}` }
              : chatMessage
          )))
        }
        if (eventName === 'pending_action' && payload.action?.id) {
          setPendingActions((currentActions) => (
            currentActions.some((action) => action.id === payload.action.id)
              ? currentActions
              : [...currentActions, payload.action]
          ))
        }
        if (eventName === 'error') {
          streamError = typeof payload.message === 'string' ? payload.message : 'Не вдалося сформувати відповідь.'
        }
      }

      while (true) {
        const { done, value } = await reader.read()
        buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })
        buffer = consumeSseEvents(buffer, handleEvent)
        if (done) {
          break
        }
      }

      if (streamError) {
        throw new Error(streamError)
      }
      await loadChatThreads()
    } catch (requestError) {
      setChatMessages((currentMessages) => currentMessages.filter((chatMessage) => chatMessage.id !== pendingId))
      setChatError(requestError.message || 'Не вдалося сформувати відповідь AI-помічника.')
    } finally {
      setChatStatus('idle')
    }
  }

  async function resolvePendingAction(actionId, operation) {
    if (!activeProjectId || resolvingActionId) {
      return
    }

    setResolvingActionId(actionId)
    setChatError('')
    try {
      const response = await fetch(`/api/ai/actions/${actionId}/${operation}?project_id=${activeProjectId}`, {
        method: 'POST',
      })
      if (!response.ok) {
        const body = await response.json().catch(() => null)
        throw new Error(typeof body?.detail === 'string' ? body.detail : 'Не вдалося обробити запропоновану дію.')
      }
      setPendingActions((currentActions) => currentActions.filter((action) => action.id !== actionId))
      if (operation === 'confirm') {
        setAnalysis(null)
        setAnalysisStatus('idle')
        await loadDashboard(false)
      }
      await loadChatThreads()
    } catch (requestError) {
      setChatError(requestError.message || 'Не вдалося обробити запропоновану дію.')
    } finally {
      setResolvingActionId(null)
    }
  }

  const mainCategoryTotals = useMemo(() => {
    const totals = new Map([['Роботи', 0], ['Матеріали', 0]])
    transactions
      .filter((transaction) => transaction.transaction_type === 'expense')
      .forEach((transaction) => {
        if (totals.has(transaction.main_category)) {
          totals.set(transaction.main_category, totals.get(transaction.main_category) + Number(transaction.amount))
        }
      })
    return sortTotals(new Map([...totals].filter(([, amount]) => amount > 0)))
  }, [transactions])

  const workSubcategoryTotals = useMemo(
    () => getSubcategoryTotals(transactions, 'Роботи'),
    [transactions],
  )
  const materialSubcategoryTotals = useMemo(
    () => getSubcategoryTotals(transactions, 'Матеріали'),
    [transactions],
  )
  const visibleTransactions = transactionFilter === 'all'
    ? transactions
    : transactions.filter((transaction) => transaction.transaction_type === transactionFilter)
  const displayedTransactions = visibleTransactions.slice(0, visibleTransactionLimit)
  const hasMoreTransactions = displayedTransactions.length < visibleTransactions.length
  const isLoading = status === 'loading'

  if (authStatus === 'guest' && guestSession) {
    return <GuestDashboard session={guestSession} onLogout={logoutGuest} />
  }

  return (
    <main className="dashboard-shell">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">Apartment finance</p>
          <h1>Фінансовий огляд ремонту</h1>
          <p className="subtitle">Контролюйте бюджет, витрати та баланс в одному місці.</p>
        </div>
        <div className="header-actions">
          {authStatus === 'authenticated' ? (
            <>
              <div className="authenticated-user">
                <span className="user-avatar" aria-hidden="true">
                  {telegramAvatarUrl ? <img src={telegramAvatarUrl} alt="" /> : '👤'}
                </span>
                <span>
                  <strong>{telegramUsername ? `@${telegramUsername}` : 'Користувач Telegram'}</strong>
                  <small>Telegram ID: {telegramId}</small>
                </span>
              </div>
              <button className="refresh-button" type="button" onClick={() => loadDashboard()} disabled={isLoading || !activeProjectId}>
                {isLoading ? 'Оновлюємо…' : '↻ Оновити дані'}
              </button>
              <button className="logout-button" type="button" onClick={logout}>Вийти</button>
            </>
          ) : (
            <div className="telegram-form login-form">
              {telegramLogin ? (
                <a className="telegram-login-link" href={telegramLogin.telegram_url} target="_blank" rel="noreferrer">
                  <span>Відкрити Telegram-бота</span>
                  <strong>@{telegramLogin.bot_username}</strong>
                </a>
              ) : (
                <span className="telegram-login-link telegram-login-link-loading">
                  {authError ? 'Не вдалося створити посилання' : 'Готуємо захищене посилання…'}
                </span>
              )}
              <p className="telegram-login-help">Відкрийте бота та підтвердьте вхід. Сайт увійде автоматично — Telegram ID і код не потрібні.</p>
              <button className="guest-login-toggle" type="button" onClick={() => setShowGuestLogin((value) => !value)}>Гостьовий вхід</button>
              {authError && (
                <button
                  className="telegram-login-retry"
                  type="button"
                  onClick={() => setLoginAttempt((attempt) => attempt + 1)}
                  disabled={authStatus === 'checking'}
                >
                  Створити нове посилання
                </button>
              )}
              {showGuestLogin && <form className="guest-login-form" onSubmit={submitGuestLogin}>
                <input value={guestLogin} onChange={(event) => setGuestLogin(event.target.value)} placeholder="Логін для перегляду" required />
                <input type="password" value={guestPassword} onChange={(event) => setGuestPassword(event.target.value)} placeholder="Пароль" required />
                <button type="submit">Увійти лише для перегляду</button>
                {guestError && <small role="alert">{guestError}</small>}
              </form>}
            </div>
          )}
        </div>
      </header>

      {authStatus === 'authenticated' && (
        <section className="panel project-switcher">
          <div>
            <p className="panel-kicker">Фінансові огляди</p>
            <div className="project-tabs" role="tablist" aria-label="Фінансові проєкти">
              {projects.map((project) => (
                <button
                  className={project.id === activeProjectId ? 'project-tab project-tab-active' : 'project-tab'}
                  key={project.id}
                  role="tab"
                  aria-selected={project.id === activeProjectId}
                  type="button"
                  onClick={() => {
                    setActiveProjectId(project.id)
                    setAnalysis(null)
                    setAnalysisStatus('idle')
                    setVisibleTransactionLimit(TRANSACTION_PAGE_SIZE)
                  }}
                >
                  {project.name}
                </button>
              ))}
            </div>
            <button
              className="export-button"
              type="button"
              onClick={exportTransactions}
              disabled={!activeProjectId || isExporting}
            >
              {isExporting ? 'Готуємо CSV…' : '⇩ Експортувати CSV'}
            </button>
            <button className="export-button" type="button" onClick={createGuestAccess} disabled={!activeProjectId}>Створити гостьовий доступ</button>
            {guestCredential && <div className="guest-credentials"><strong>{guestCredential.password ? 'Передайте лише цій людині:' : 'Активний гостьовий доступ:'}</strong><span>Логін: <code>{guestCredential.login}</code></span>{guestCredential.password && <span>Пароль: <code>{guestCredential.password}</code></span>}<button type="button" onClick={revokeGuestAccess}>Відкликати доступ</button></div>}
            {guestAccesses.length > 0 && <div className="guest-access-list">Активні: {guestAccesses.map((access) => <button key={access.id} type="button" onClick={() => setGuestCredential(access)}>{access.login}</button>)}</div>}
            {guestAccessError && <p className="transaction-form-error" role="alert">{guestAccessError}</p>}
          </div>
          <form className="new-project-form" onSubmit={createProject}>
            <label htmlFor="new-project-name">Новий огляд</label>
            <input
              id="new-project-name"
              maxLength="150"
              value={newProjectName}
              onChange={(event) => setNewProjectName(event.target.value)}
              placeholder="Наприклад, Будинок у Львові"
              disabled={isCreatingProject}
            />
            <button type="submit" disabled={isCreatingProject}>
              {isCreatingProject ? 'Створюємо…' : '+ Створити'}
            </button>
          </form>
          {projectError && <p className="transaction-form-error" role="alert">{projectError}</p>}
          {exportError && <p className="transaction-form-error" role="alert">{exportError}</p>}
        </section>
      )}

      {authStatus === 'unauthenticated' && (
        <section className="message-card bind-card">
          <h2>Увійдіть через Telegram</h2>
          <p>Натисніть «Відкрити Telegram-бота» вище та підтвердьте вхід у боті. Сторінка отримає сесію автоматично; посилання діє 5 хвилин і лише для цього браузера.</p>
          {authError && <p className="transaction-form-error" role="alert">{authError}</p>}
        </section>
      )}

      {status === 'error' && (
        <section className="message-card error-card" aria-live="polite">
          <h2>Не вдалося підключитися до API</h2>
          <p>{error}</p>
          <p className="hint">Переконайтеся, що API запущено на <code>http://localhost:8000</code>, і спробуйте ще раз.</p>
          <button className="retry-button" type="button" onClick={loadDashboard}>Спробувати ще раз</button>
        </section>
      )}

      <section className="summary-grid" aria-label="Фінансові показники">
            <article className="summary-card income-card">
              <span className="card-label">Загальний дохід</span>
              <strong>{isLoading ? '—' : formatCurrency(summary?.total_income)}</strong>
              <span className="card-note">Надходження за весь період</span>
            </article>
            <article className="summary-card expense-card">
              <span className="card-label">Загальні витрати</span>
              <strong>{isLoading ? '—' : formatCurrency(summary?.total_expense)}</strong>
              <span className="card-note">Витрати на ремонт</span>
            </article>
            <article className="summary-card balance-card">
              <span className="card-label">Поточний баланс</span>
              <strong>{isLoading ? '—' : formatCurrency(summary?.balance)}</strong>
              <span className="card-note">Дохід мінус витрати</span>
            </article>
      </section>

      <section className="panel ai-chat-panel" aria-label="AI-помічник">
        <div className="panel-heading ai-chat-heading">
          <div>
            <p className="panel-kicker">Gemini AI · контрольовані дії</p>
            <h2>AI-помічник фінансів</h2>
            <p className="ai-chat-retention">Контекст діалогу зберігається 7 днів. Будь-яка запропонована зміна виконується лише після вашого підтвердження.</p>
          </div>
          <button className="new-chat-button" type="button" onClick={startNewChat} disabled={chatStatus === 'sending' || !activeProjectId}>
            + Новий чат
          </button>
        </div>

        <div className="ai-chat-layout">
          <aside className="chat-thread-list" aria-label="Попередні діалоги">
            <p className="chat-thread-label">Попередні діалоги</p>
            {chatThreads.length > 0 ? chatThreads.map((thread) => (
              <button
                className={thread.id === selectedChatThreadId ? 'chat-thread-button chat-thread-button-active' : 'chat-thread-button'}
                key={thread.id}
                type="button"
                onClick={() => { void selectChatThread(thread.id) }}
                disabled={chatStatus === 'sending'}
              >
                <strong>{thread.title}</strong>
                <small>{formatDate(thread.updated_at)}</small>
              </button>
            )) : <p className="chat-thread-empty">Тут з’являться ваші діалоги.</p>}
          </aside>

          <div className="chat-conversation">
            <div className="chat-messages" aria-live="polite">
              {chatStatus === 'loading' ? <p className="chat-placeholder">Завантажуємо діалог…</p> : null}
              {chatStatus !== 'loading' && chatMessages.length === 0 ? (
                <div className="chat-placeholder">
                  <strong>Запитайте про фінанси цього проєкту</strong>
                  <span>Наприклад: «Проаналізуй витрати за червень» або «Покажи найбільші ризики».</span>
                </div>
              ) : null}
              {chatMessages.map((message) => (
                <article className={`chat-message chat-message-${message.role}`} key={message.id}>
                  <span>{message.role === 'user' ? 'Ви' : 'AI-помічник'}</span>
                  <p>{message.content || (chatStatus === 'sending' ? 'Формуємо відповідь…' : '')}</p>
                </article>
              ))}
            </div>
            {pendingActions.length > 0 && (
              <section className="pending-actions" aria-label="Запропоновані AI-дії">
                {pendingActions.map((action) => (
                  <article className="pending-action-card" key={action.id}>
                    <div className="pending-action-heading">
                      <div>
                        <p className="panel-kicker">Запропонована дія</p>
                        <h3>{action.payload.type === 'expense' ? 'Створити витрату' : 'Створити дохід'}</h3>
                      </div>
                      <span className="pending-action-status">Потрібне підтвердження</span>
                    </div>
                    <dl className="pending-action-details">
                      <div><dt>Сума</dt><dd>{formatCurrency(action.payload.amount)}</dd></div>
                      <div><dt>Категорія</dt><dd>{action.payload.category} → {action.payload.subcategory}</dd></div>
                      <div><dt>Дата</dt><dd>{formatDate(action.payload.date)}</dd></div>
                      <div><dt>Курс USD</dt><dd>{formatExchangeRate(action.payload.exchange_rate)} грн/$</dd></div>
                      <div className="pending-action-description"><dt>Опис</dt><dd>{action.payload.description}</dd></div>
                    </dl>
                    <div className="pending-action-buttons">
                      <button
                        className="confirm-action-button"
                        type="button"
                        onClick={() => { void resolvePendingAction(action.id, 'confirm') }}
                        disabled={Boolean(resolvingActionId) || chatStatus === 'sending'}
                      >
                        {resolvingActionId === action.id ? 'Обробляємо…' : 'Підтвердити'}
                      </button>
                      <button
                        className="cancel-action-button"
                        type="button"
                        onClick={() => { void resolvePendingAction(action.id, 'cancel') }}
                        disabled={Boolean(resolvingActionId) || chatStatus === 'sending'}
                      >
                        Скасувати
                      </button>
                    </div>
                  </article>
                ))}
              </section>
            )}
            {chatError && <p className="transaction-form-error chat-error" role="alert">{chatError}</p>}
            <form className="chat-input-form" onSubmit={sendChatMessage}>
              <label className="visually-hidden" htmlFor="ai-chat-message">Запит до AI-помічника</label>
              <textarea
                id="ai-chat-message"
                maxLength="2000"
                value={chatInput}
                onChange={(event) => setChatInput(event.target.value)}
                placeholder="Напишіть запит про операції…"
                disabled={!activeProjectId || chatStatus === 'sending' || chatStatus === 'loading'}
                rows="2"
              />
              <button type="submit" disabled={!chatInput.trim() || !activeProjectId || chatStatus === 'sending' || chatStatus === 'loading'}>
                {chatStatus === 'sending' ? 'Відповідаємо…' : 'Надіслати'}
              </button>
            </form>
          </div>
        </div>
      </section>

      <section className="panel ai-analysis-panel">
        <div className="panel-heading">
          <div>
            <p className="panel-kicker">Gemini AI</p>
            <h2>Аналіз фінансових операцій</h2>
          </div>
          <button
            className="ai-analysis-button"
            type="button"
            onClick={runAiAnalysis}
            disabled={!telegramId || !activeProjectId || analysisStatus === 'loading'}
          >
            {analysisStatus === 'loading' ? 'Аналізуємо…' : 'Запустити AI-аналіз'}
          </button>
        </div>

        {analysis && analysisStatus === 'ready' && (
          <p className="ai-analysis-cache-status">
            {analysis.cached ? 'Показано збережений AI-аналіз для поточних даних.' : 'AI-аналіз щойно оновлено.'}
          </p>
        )}
        {analysisStatus === 'loading' && <p className="ai-analysis-loading">Gemini аналізує ваші операції…</p>}
        {analysisStatus === 'error' && <p className="transaction-form-error" role="alert">{analysisError}</p>}

        {analysis && analysisStatus === 'ready' && (
          <div className="ai-analysis-results">
            <article>
              <h3>Загальний висновок</h3>
              <p>{analysis.summary}</p>
            </article>
            <article>
              <h3>Основні категорії витрат</h3>
              {analysis.expense_categories.length > 0 ? (
                <ul className="ai-analysis-list category-analysis-list">
                  {analysis.expense_categories.map((category) => (
                    <li className="category-analysis-group" key={category.category}>
                      <div className="category-analysis-main">
                        <strong>{category.category}</strong>
                        <strong>{formatCurrency(category.amount)}</strong>
                      </div>
                      <ul className="category-analysis-subcategories">
                        {(category.subcategories ?? []).map((subcategory) => (
                          <li key={subcategory.category}>
                            <span>{subcategory.category}</span>
                            <strong>{formatCurrency(subcategory.amount)}</strong>
                          </li>
                        ))}
                      </ul>
                    </li>
                  ))}
                </ul>
              ) : <p>Витрат для аналізу поки немає.</p>}
            </article>
            <article>
              <h3>Можливі ризики</h3>
              {analysis.risks.length > 0 ? <ul className="ai-analysis-list">{analysis.risks.map((risk) => <li key={risk}>{risk}</li>)}</ul> : <p>Явних ризиків не виявлено.</p>}
            </article>
            <article>
              <h3>Практичні поради</h3>
              {analysis.recommendations.length > 0 ? <ul className="ai-analysis-list">{analysis.recommendations.map((recommendation) => <li key={recommendation}>{recommendation}</li>)}</ul> : <p>Поради з’являться після додавання операцій.</p>}
            </article>
          </div>
        )}
      </section>

      <section className="panel transaction-form-panel">
        <div className="panel-heading">
          <div>
            <p className="panel-kicker">Нова операція</p>
            <h2>Додайте до бюджету</h2>
          </div>
        </div>
        <form className="transaction-form" onSubmit={submitTransaction}>
          <label>
            Тип
            <select name="type" value={transactionForm.type} onChange={updateTransactionForm}>
              <option value="expense">Витрата</option>
              <option value="income">Дохід</option>
            </select>
          </label>
          <label>
            Сума, грн
            <input name="amount" type="number" min="0.01" step="0.01" value={transactionForm.amount} onChange={updateTransactionForm} required />
          </label>
          <label>
            Курс USD, грн
            <input name="exchange_rate" type="number" min="0.0001" step="0.0001" value={transactionForm.exchange_rate} onChange={updateTransactionForm} placeholder="Наприклад, 41.50" required />
          </label>
          <label>
            Категорія
            <select name="category" value={transactionForm.category} onChange={updateTransactionForm}>
              <option value="Роботи">Роботи</option>
              <option value="Матеріали">Матеріали</option>
            </select>
          </label>
          <label>
            Підкатегорія
            <input name="subcategory" maxLength="100" value={transactionForm.subcategory} onChange={updateTransactionForm} placeholder="Наприклад, Сантехніка, електрика, стіни або меблі" required />
          </label>
          <label>
            Позиція
            <input name="description" maxLength="255" value={transactionForm.description} onChange={updateTransactionForm} placeholder="Наприклад, Кабель ВВГнг 3×2,5 або монтаж розеток" required />
          </label>
          <label>
            Дата
            <input name="date" type="date" value={transactionForm.date} onChange={updateTransactionForm} required />
          </label>
          <button className="submit-transaction-button" type="submit" disabled={isSubmittingTransaction}>
            {isSubmittingTransaction ? 'Зберігаємо…' : 'Додати операцію'}
          </button>
        </form>
        {transactionError && <p className="transaction-form-error" role="alert">{transactionError}</p>}
      </section>

      <section className="content-grid">
        <section className="panel chart-panel">
          <div className="panel-heading">
            <div>
              <p className="panel-kicker">Структура витрат</p>
              <h2>Основні категорії витрат</h2>
            </div>
            <span className="live-indicator"><i /> Дані з API</span>
          </div>
          <div className="distribution-grid">
            <ExpenseDistributionChart
              title="Роботи / матеріали"
              description="Загальний розподіл"
              totals={mainCategoryTotals}
              isLoading={isLoading}
              emptyMessage="Ще немає витрат за основними категоріями."
            />
            <ExpenseDistributionChart
              title="Роботи за підкатегоріями"
              description="Деталізація робіт"
              totals={workSubcategoryTotals}
              isLoading={isLoading}
              emptyMessage="Ще немає витрат у категорії «Роботи»."
            />
            <ExpenseDistributionChart
              title="Матеріали за підкатегоріями"
              description="Деталізація матеріалів"
              totals={materialSubcategoryTotals}
              isLoading={isLoading}
              emptyMessage="Ще немає витрат у категорії «Матеріали»."
            />
          </div>
        </section>

            <article className="panel recent-panel">
              <div className="panel-heading">
                <div>
                  <p className="panel-kicker">Історія</p>
                  <h2>Останні операції</h2>
                </div>
                <span className="transaction-count">{visibleTransactions.length}</span>
              </div>

              <div className="transaction-filters" role="group" aria-label="Фільтр операцій">
                {[
                  ['all', 'Усі'],
                  ['income', 'Доходи'],
                  ['expense', 'Витрати'],
                ].map(([filter, label]) => (
                  <button
                    className={transactionFilter === filter ? 'filter-button filter-button-active' : 'filter-button'}
                    key={filter}
                    type="button"
                    onClick={() => {
                      setTransactionFilter(filter)
                      setVisibleTransactionLimit(TRANSACTION_PAGE_SIZE)
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {isLoading ? (
                <div className="list-placeholder">Завантажуємо операції…</div>
              ) : visibleTransactions.length > 0 ? (
                <>
                  <div className="transaction-table-wrapper">
                  <table className="transaction-table">
                    <caption>Останні фінансові операції</caption>
                    <thead>
                      <tr>
                        <th scope="col">Дата</th>
                        <th scope="col">Тип</th>
                        <th scope="col">Категорія</th>
                        <th scope="col">Підкатегорія</th>
                        <th scope="col">Позиція</th>
                        <th scope="col" className="amount-heading">Сума, грн</th>
                        <th scope="col" className="amount-heading">Сума, $</th>
                        <th scope="col"><span className="visually-hidden">Дія</span></th>
                      </tr>
                    </thead>
                    <tbody>
                      {displayedTransactions.map((transaction) => {
                        const isIncome = transaction.transaction_type === 'income'

                        return (
                          <tr key={transaction.id}>
                            <td className="date-cell">{formatDate(transaction.created_at)}</td>
                            <td>
                              <span className={isIncome ? 'type-pill income-pill' : 'type-pill expense-pill'}>
                                {isIncome ? 'Дохід' : 'Витрата'}
                              </span>
                            </td>
                            <td>{transaction.main_category ?? '—'}</td>
                            <td>{transaction.subcategory}</td>
                            <td className="position-cell">{transaction.description ?? '—'}</td>
                            <td className={isIncome ? 'table-amount income-amount' : 'table-amount'}>
                              {isIncome ? '+' : '−'}{formatCurrency(transaction.amount)}
                            </td>
                            <td className={isIncome ? 'table-amount usd-amount income-amount' : 'table-amount usd-amount'}>
                              {transaction.amount_usd == null ? '—' : <>
                                {isIncome ? '+' : '−'}{formatUsd(transaction.amount_usd)}
                                <small>Курс: {formatExchangeRate(transaction.exchange_rate)} грн/$</small>
                              </>}
                            </td>
                            <td className="transaction-action-cell">
                              <button
                                className="delete-transaction-button"
                                type="button"
                                onClick={() => deleteTransaction(transaction.id)}
                                disabled={deletingTransactionId === transaction.id}
                              >
                                {deletingTransactionId === transaction.id ? 'Видаляємо…' : 'Видалити'}
                              </button>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                  </div>
                  {hasMoreTransactions && (
                    <div className="transaction-pagination">
                      <p>Показано {displayedTransactions.length} з {visibleTransactions.length} операцій</p>
                      <div>
                        <button
                          className="show-more-button"
                          type="button"
                          onClick={() => setVisibleTransactionLimit((limit) => limit + TRANSACTION_PAGE_SIZE)}
                        >
                          Показати ще
                        </button>
                        <button
                          className="show-all-button"
                          type="button"
                          onClick={() => setVisibleTransactionLimit(visibleTransactions.length)}
                        >
                          Показати всі
                        </button>
                      </div>
                    </div>
                  )}
                </>
              ) : (
                <div className="empty-state">За цим фільтром операцій поки немає.</div>
              )}
              {transactionActionError && <p className="transaction-form-error" role="alert">{transactionActionError}</p>}
            </article>
      </section>
    </main>
  )
}

export default App
