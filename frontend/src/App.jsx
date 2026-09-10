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

function formatCurrency(value) {
  return currencyFormatter.format(Number(value ?? 0))
}

function formatDate(value) {
  return value ? dateFormatter.format(new Date(value)) : '—'
}

function App() {
  const [summary, setSummary] = useState(null)
  const [transactions, setTransactions] = useState([])
  const [status, setStatus] = useState('loading')
  const [error, setError] = useState('')

  const loadDashboard = useCallback(async (showLoading = true) => {
    if (showLoading) {
      setStatus('loading')
      setError('')
    }

    try {
      const [summaryResponse, transactionsResponse] = await Promise.all([
        fetch('/api/summary'),
        fetch('/api/transactions'),
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
  }, [])

  useEffect(() => {
    const loadTimer = window.setTimeout(() => loadDashboard(false), 0)
    return () => window.clearTimeout(loadTimer)
  }, [loadDashboard])

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
        <button className="refresh-button" type="button" onClick={loadDashboard} disabled={isLoading}>
          {isLoading ? 'Оновлюємо…' : '↻ Оновити дані'}
        </button>
      </header>

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
                  {categoryTotals.map((category) => (
                    <div className="bar-column" key={category.label}>
                      <span className="bar-value">{formatCurrency(category.amount)}</span>
                      <div className="bar-track">
                        <div
                          className="bar-fill"
                          style={{ height: `${Math.max((category.amount / largestCategoryTotal) * 100, 7)}%` }}
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
                <div className="transaction-list">
                  {transactions.slice(0, 5).map((transaction) => (
                    <div className="transaction-row" key={transaction.id}>
                      <div className="category-mark">{transaction.main_category?.[0] ?? '•'}</div>
                      <div className="transaction-details">
                        <strong>{transaction.description ?? transaction.subcategory}</strong>
                        <span>
                          {transaction.main_category ?? 'Категорія'} · {transaction.subcategory} · {formatDate(transaction.created_at)}
                        </span>
                      </div>
                      <strong className={transaction.transaction_type === 'income' ? 'transaction-amount income-amount' : 'transaction-amount'}>
                        {transaction.transaction_type === 'income' ? '+' : '−'}{formatCurrency(transaction.amount)}
                      </strong>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="empty-state">Операцій поки немає.</div>
              )}
            </article>
      </section>
    </main>
  )
}

export default App
