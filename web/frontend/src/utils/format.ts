export function money(value: number, signed = false) {
  return (signed && value > 0 ? '+' : value < 0 ? '-' : '') + '¥ ' + Math.abs(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
export function percent(value: number, signed = true) { return (signed && value > 0 ? '+' : '') + value.toFixed(2) + '%' }
export function number(value: number, digits = 2) { return value.toLocaleString('zh-CN', { maximumFractionDigits: digits }) }
let displayTimezone = 'Asia/Shanghai'
export function setDisplayTimezone(value: string) { displayTimezone = ['Asia/Shanghai', 'Asia/Tokyo'].includes(value) ? value : 'Asia/Shanghai' }
export function dateTime(value: string | number = Date.now()) { return new Intl.DateTimeFormat('zh-CN', { timeZone: displayTimezone, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(value)) }
export function localDateInput() {
  const parts = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date())
  return parts.replace(' ', 'T')
}
export function downloadFile(name: string, content: string, type = 'text/plain;charset=utf-8') {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const link = document.createElement('a')
  link.href = url; link.download = name; document.body.append(link); link.click(); link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
export function csvText(headers: string[], rows: (string | number)[][]) {
  return '\uFEFF' + [headers, ...rows].map(row => row.map(value => {
    let text = String(value)
    if (/^[=+@-]/.test(text) && !/^[+-]?\d[\d,.%]*$/.test(text)) text = "'" + text
    return '"' + text.replace(/"/g, '""') + '"'
  }).join(',')).join('\r\n')
}
export function downloadCsv(name: string, headers: string[], rows: (string | number)[][]) { downloadFile(name, csvText(headers, rows), 'text/csv;charset=utf-8') }
