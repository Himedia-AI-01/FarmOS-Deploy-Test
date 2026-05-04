import { useState } from 'react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { formatPrice } from '@/lib/utils';

interface RevenueChartProps {
  data: { date: string; revenue: number }[];
}

type ViewMode = 'daily' | 'weekly';

export default function RevenueChart({ data }: RevenueChartProps) {
  const [viewMode, setViewMode] = useState<ViewMode>('weekly');

  return (
    <div className="bg-white p-8 rounded-3xl shadow-sm space-y-6 border border-stone-100">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-bold text-stone-900">주간 매출 현황</h3>
          <p className="text-xs text-stone-400">지난 7일간의 누적 데이터</p>
        </div>
        <div className="flex gap-1 bg-stone-50 p-1 rounded-xl">
          <button
            type="button"
            onClick={() => setViewMode('daily')}
            className={`px-4 py-1.5 text-[10px] font-bold uppercase tracking-wider rounded-lg transition-all ${
              viewMode === 'daily'
                ? 'bg-white text-emerald-700 shadow-sm border border-stone-100'
                : 'text-stone-500 hover:text-emerald-600'
            }`}
          >
            Daily
          </button>
          <button
            type="button"
            onClick={() => setViewMode('weekly')}
            className={`px-4 py-1.5 text-[10px] font-bold uppercase tracking-wider rounded-lg transition-all ${
              viewMode === 'weekly'
                ? 'bg-white text-emerald-700 shadow-sm border border-stone-100'
                : 'text-stone-500 hover:text-emerald-600'
            }`}
          >
            Weekly
          </button>
        </div>
      </div>

      {/* Chart */}
      {data.length === 0 ? (
        <div className="h-[240px] flex items-center justify-center text-stone-400 text-sm">
          데이터가 없습니다
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={240}>
          <AreaChart data={data} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="revenueGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#006933" stopOpacity={0.15} />
                <stop offset="95%" stopColor="#006933" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f5" vertical={false} />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 10, fontWeight: 700, fill: '#a8a29e' }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              tick={{ fontSize: 10, fill: '#a8a29e' }}
              axisLine={false}
              tickLine={false}
              tickFormatter={(v: number) =>
                v >= 10000 ? `${(v / 10000).toFixed(0)}만` : `${v}`
              }
              width={40}
            />
            <Tooltip
              contentStyle={{
                borderRadius: '12px',
                border: '1px solid #e7e5e4',
                boxShadow: '0 4px 16px -4px rgba(0,0,0,0.08)',
                fontSize: 12,
              }}
              formatter={(value) => [formatPrice(Number(value)), '매출']}
              labelStyle={{ fontWeight: 700, color: '#191c1d' }}
            />
            <Area
              type="monotone"
              dataKey="revenue"
              stroke="#006933"
              strokeWidth={2.5}
              fill="url(#revenueGradient)"
              dot={{ r: 4, fill: '#006933', strokeWidth: 0 }}
              activeDot={{ r: 6, fill: '#006933', strokeWidth: 0 }}
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
