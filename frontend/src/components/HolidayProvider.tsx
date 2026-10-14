import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { holidayApi, type HolidayYearResponse } from '../api/client'
import type { HolidayTable } from '../calendar/holidays'

/**
 * 节假日数据（按年）的加载与共享。
 *
 * 【为什么要 Provider】
 * 日期面板一次渲染 42 个格子，每个格子都要问"这天放假吗"。如果每个格子自己去请求，
 * 就是一个面板打 42 次接口。这里统一按**年份**去重：同一年只请求一次，所有格子共享。
 *
 * 【拿不到数据的处理】
 * 静默忽略 —— 面板照常能用，只是不标「休/班」。**绝不能因为节假日接口出问题让日历崩掉**：
 * 用户是在现场选观测日期，那才是正事。
 */

interface HolidayContextValue {
  tables: Record<number, HolidayTable>
  /** 确保某年数据已请求（按年去重，随便重复调用） */
  ensure: (year: number) => void
}

const HolidayContext = createContext<HolidayContextValue>({ tables: {}, ensure: () => {} })

function toTable(res: HolidayYearResponse): HolidayTable {
  return {
    year: res.year,
    rest: Object.fromEntries(res.rest.map((x) => [x.date, x.name])),
    makeup: Object.fromEntries(res.makeup.map((x) => [x.date, x.name])),
    source: res.source,
    fetchedAt: res.fetched_at,
  }
}

export function HolidayProvider({ children }: { children: ReactNode }) {
  const [tables, setTables] = useState<Record<number, HolidayTable>>({})
  const requested = useRef<Set<number>>(new Set())

  const ensure = useCallback((year: number) => {
    if (requested.current.has(year)) return
    requested.current.add(year)
    holidayApi
      .get(year)
      .then((res) => setTables((prev) => ({ ...prev, [year]: toTable(res) })))
      .catch(() => {
        // 失败就当作"没有节假日数据"。允许下次再试（否则一次网络抖动会记一整轮）。
        requested.current.delete(year)
      })
  }, [])

  // 启动时先取当年与次年 —— 现场最常看的就是这两个
  useEffect(() => {
    const y = new Date().getFullYear()
    ensure(y)
    ensure(y + 1)
  }, [ensure])

  const value = useMemo(() => ({ tables, ensure }), [tables, ensure])
  return <HolidayContext.Provider value={value}>{children}</HolidayContext.Provider>
}

/** 取某年的节假日表（未加载 / 该年无数据时为 undefined） */
export function useHolidayTable(year: number): HolidayTable | undefined {
  const t = useContext(HolidayContext).tables[year]
  return t && (t.source !== 'none' || Object.keys(t.rest).length > 0 || Object.keys(t.makeup).length > 0)
    ? t
    : undefined
}

/** 触发某年数据加载（幂等；放在 effect 里调用即可） */
export function useHolidayEnsure(): (year: number) => void {
  return useContext(HolidayContext).ensure
}

/** 已加载的年份概况（图例里告诉用户"数据是哪年的、从哪来的"） */
export function useHolidayYears(): HolidayTable[] {
  const tables = useContext(HolidayContext).tables
  return useMemo(() => Object.values(tables).sort((a, b) => a.year - b.year), [tables])
}
