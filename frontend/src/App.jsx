import { useCallback, useEffect, useMemo, useState } from 'react'
import './App.css'

const currencyFormatter = new Intl.NumberFormat('uk-UA', {
  style: 'currency',
  currency: 'UAH',
  maximumFractionDigits: 2,
})

const dateFormatter = new Intl.DateTimeFormat('uk-UA', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
})

const barColors = ['#c65b3f', '#d5864c', '#8d9a70', '#556b63', '#b6a176', '#7d6656']

function getTodayForInput() {
  const now = new Date()
  const timezoneOffset = now.getTimezoneOffset() * 60_000
  return new Date(now.getTime() - timezoneOffset).toISOString().slice(0, 10)
}

function createEmptyTransactionForm() {
  return {
    type: 'expense',
    amount: '',
    category: '',
    description: '',
    date: getTodayForInput(),
  }
}

function formatCurrency(value) {
  return currencyFormatter.format(Number(value ?? 0))
}

function formatDate(value) {
  return value ? dateFormatter.format(new Date(value)) : '—'
}

function App() {
  const [telegramId, setTelegramId] = useState(() => localStorage.getItem('telegram_id') ?? '')
  const [telegramIdInput, setTelegramIdInput] = useState(() => localStorage.getItem('telegram_id') ?? '')
  const [summary, setSummary] = useState(null)
  const [transactions, setTransactions] = useState([])
  const [status, setStatus] = useState(() => (localStorage.getItem('telegram_id') ? 'loading' : 'idle'))
  const [error, setError] = useState('')
  const [transactionForm, setTransactionForm] = useState(createEmptyTransactionForm)
  const [transactionError, setTransactionError] = useState('')
  const [isSubmittingTransaction, setIsSubmittingTransaction] = useState(false)
  const [transactionActionError, setTransactionActionError] = useState('')
  const [deletingTransactionId, setDeletingTransactionId] = useState(null)

  const loadDashboard = useCallback(async (showLoading = true) => {
    if (!telegramId) {
      return
    }

    if (showLoading) {
      setStatus('loading')
      setError('')
    }

    try {
      const [summaryResponse, transactionsResponse] = await Promise.all([
        fetch(`/api/summary?telegram_id=${encodeURIComponent(telegramId)}`),
        fetch(`/api/transactions?telegram_id=${encodeURIComponent(telegramId)}`),
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
  }, [telegramId])

  useEffect(() => {
    if (!telegramId) {
      return undefined
    }

    const loadTimer = window.setTimeout(() => loadDashboard(false), 0)
    return () => window.clearTimeout(loadTimer)
  }, [loadDashboard, telegramId])

  function bindTelegramId(event) {
    event.preventDefault()
    const normalizedTelegramId = telegramIdInput.trim()
    if (!/^\d+$/.test(normalizedTelegramId) || normalizedTelegramId === '0') {
      setError('Введіть коректний Telegram ID із команди /id у боті.')
      setStatus('error')
      return
    }

    localStorage.setItem('telegram_id', normalizedTelegramId)
    setTelegramId(normalizedTelegramId)
    setError('')
    setStatus('loading')
  }

  function updateTransactionForm(event) {
    const { name, value } = event.target
    setTransactionForm((currentForm) => ({ ...currentForm, [name]: value }))
  }

  async function submitTransaction(event) {
    event.preventDefault()
    if (!telegramId) {
      setTransactionError('Спочатку підключіть Telegram ID, щоб зберігати операції у своєму обліку.')
      return
    }

    setIsSubmittingTransaction(true)
    setTransactionError('')

    try {
      const response = await fetch('/api/transactions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          telegram_id: Number(telegramId),
          ...transactionForm,
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
      await loadDashboard()
    } catch (requestError) {
      setTransactionError(requestError.message || 'Не вдалося зберегти операцію. Спробуйте ще раз.')
    } finally {
      setIsSubmittingTransaction(false)
    }
  }

  async function deleteTransaction(transactionId) {
    if (!telegramId || !window.confirm('Видалити операцію?')) {
      return
    }

    setDeletingTransactionId(transactionId)
    setTransactionActionError('')

    try {
      const response = await fetch(
        `/api/transactions/${transactionId}?telegram_id=${encodeURIComponent(telegramId)}`,
        { method: 'DELETE' },
      )

      if (!response.ok) {
        const responseBody = await response.json().catch(() => null)
        throw new Error(
          typeof responseBody?.detail === 'string'
            ? responseBody.detail
            : 'Не вдалося видалити операцію. Спробуйте ще раз.',
        )
      }

      await loadDashboard()
    } catch (requestError) {
      setTransactionActionError(requestError.message || 'Не вдалося видалити операцію. Спробуйте ще раз.')
    } finally {
      setDeletingTransactionId(null)
    }
  }

  const categoryTotals = useMemo(() => {
    const totals = new Map()

    transactions
      .filter((transaction) => transaction.transaction_type === 'expense')
      .forEach((transaction) => {
      const mainCategory = transaction.main_category ?? 'Без основної категорії'
      const subcategory = transaction.subcategory
      const label = `${mainCategory} · ${subcategory}`
        totals.set(label, (totals.get(label) ?? 0) + Number(transaction.amount))
      })

    return [...totals.entries()]
      .map(([label, amount]) => ({ label, amount }))
      .sort((left, right) => right.amount - left.amount)
      .slice(0, 6)
  }, [transactions])

  const largestCategoryTotal = Math.max(...categoryTotals.map((item) => item.amount), 0)
  const isLoading = status === 'loading'

  return (
    <main className="dashboard-shell">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">Apartment finance</p>
          <h1>Фінансовий огляд ремонту</h1>
          <p className="subtitle">Контролюйте бюджет, витрати та баланс в одному місці.</p>
        </div>
        <div className="header-actions">
          <form className="telegram-form" onSubmit={bindTelegramId}>
            <label htmlFor="telegram-id">Telegram ID</label>
            <input
              id="telegram-id"
              inputMode="numeric"
              value={telegramIdInput}
              onChange={(event) => setTelegramIdInput(event.target.value)}
              placeholder="Введіть через /id"
            />
            <button type="submit">Підключити</button>
          </form>
          <button className="refresh-button" type="button" onClick={() => loadDashboard()} disabled={isLoading || !telegramId}>
            {isLoading ? 'Оновлюємо…' : '↻ Оновити дані'}
          </button>
        </div>
      </header>

      {!telegramId && (
        <section className="message-card bind-card">
          <h2>Підключіть свій Telegram</h2>
          <p>Надішліть боту <code>/id</code>, скопіюйте число з відповіді та введіть його у поле Telegram ID вище.</p>
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
            Категорія
            <input name="category" maxLength="100" value={transactionForm.category} onChange={updateTransactionForm} placeholder="Наприклад, Електрика" required />
          </label>
          <label>
            Опис
            <input name="description" maxLength="255" value={transactionForm.description} onChange={updateTransactionForm} placeholder="Наприклад, Кабель" required />
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
            <article className="panel chart-panel">
              <div className="panel-heading">
                <div>
                  <p className="panel-kicker">Структура витрат</p>
                  <h2>Найбільші категорії</h2>
                </div>
                <span className="live-indicator"><i /> Дані з API</span>
              </div>

              {isLoading ? (
                <div className="chart-placeholder">Завантажуємо діаграму…</div>
              ) : categoryTotals.length > 0 ? (
                <div className="bar-chart" aria-label="Стовпчикова діаграма витрат за категоріями">
                  {categoryTotals.map((category, index) => (
                    <div className="bar-column" key={category.label}>
                      <span className="bar-value">{formatCurrency(category.amount)}</span>
                      <div className="bar-track">
                        <div
                          className="bar-fill"
                          style={{
                            height: `${Math.max((category.amount / largestCategoryTotal) * 100, 7)}%`,
                            '--bar-color': barColors[index % barColors.length],
                          }}
                        />
                      </div>
                      <span className="bar-label">{category.label}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="empty-state">Ще немає витрат. Додайте першу через Telegram-бота.</div>
              )}
            </article>

            <article className="panel recent-panel">
              <div className="panel-heading">
                <div>
                  <p className="panel-kicker">Історія</p>
                  <h2>Останні операції</h2>
                </div>
                <span className="transaction-count">{transactions.length}</span>
              </div>

              {isLoading ? (
                <div className="list-placeholder">Завантажуємо операції…</div>
              ) : transactions.length > 0 ? (
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
                        <th scope="col" className="amount-heading">Сума</th>
                        <th scope="col"><span className="visually-hidden">Дія</span></th>
                      </tr>
                    </thead>
                    <tbody>
                      {transactions.slice(0, 8).map((transaction) => {
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
              ) : (
                <div className="empty-state">Операцій поки немає.</div>
              )}
              {transactionActionError && <p className="transaction-form-error" role="alert">{transactionActionError}</p>}
            </article>
      </section>
    </main>
  )
}

export default App
