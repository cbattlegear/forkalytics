import { useState, useEffect } from 'react'
import { 
  MessageCircle, 
  Heart, 
  Repeat2, 
  Clock,
  Smile,
  Frown,
  Meh,
  ChevronLeft,
  ChevronRight,
  Calendar
} from 'lucide-react'
import { 
  LineChart, 
  Line, 
  XAxis, 
  YAxis, 
  CartesianGrid, 
  Tooltip, 
  ResponsiveContainer,
  BarChart,
  Bar,
  PieChart,
  Pie,
  Cell
} from 'recharts'

const API_URL = import.meta.env.VITE_API_URL || ''

// Helper to parse UTC dates from the API and convert to local time
// API returns dates without timezone info but they are UTC
const parseUTCDate = (dateString) => {
  if (!dateString) return null
  // If the date string doesn't have timezone info, treat it as UTC
  const hasTimezone = dateString.includes('Z') || dateString.includes('+') || dateString.includes('-', 10)
  const utcString = hasTimezone ? dateString : dateString + 'Z'
  return new Date(utcString)
}

// Format a UTC date string to local time
const formatLocalDateTime = (dateString, options = {}) => {
  const date = parseUTCDate(dateString)
  if (!date) return ''
  return date.toLocaleString(undefined, options)
}

const formatLocalDate = (dateString, options = {}) => {
  const date = parseUTCDate(dateString)
  if (!date) return ''
  return date.toLocaleDateString(undefined, options)
}

const formatLocalHour = (dateString) => {
  const date = parseUTCDate(dateString)
  if (!date) return ''
  return date.getHours() + ':00'
}

// Custom Utensil Icons
const ForkIcon = ({ className = "w-6 h-6" }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 2v7c0 1.1.9 2 2 2h4a2 2 0 0 0 2-2V2" />
    <path d="M7 2v20" />
    <path d="M3 2h8" />
    <path d="M5 2v5" />
    <path d="M9 2v5" />
  </svg>
)

const KnifeIcon = ({ className = "w-6 h-6" }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M19 2L6 15" />
    <path d="M6 15l-3 3c-.6.6-.6 1.5 0 2.1l.8.8c.6.6 1.5.6 2.1 0l3-3" />
    <path d="M19 2c2 2 2 5 0 7l-7 7" />
  </svg>
)

const SpoonIcon = ({ className = "w-6 h-6" }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <ellipse cx="12" cy="6" rx="5" ry="4" />
    <path d="M12 10v12" />
  </svg>
)

const SpatulaIcon = ({ className = "w-6 h-6" }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="6" y="2" width="8" height="6" rx="1" />
    <path d="M10 8v14" />
    <path d="M8 8h4" />
  </svg>
)

const WhiskIcon = ({ className = "w-6 h-6" }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 22V12" />
    <path d="M5 12c0-4 3-8 7-10 4 2 7 6 7 10" />
    <path d="M7 12c0-3 2.2-6 5-7.5 2.8 1.5 5 4.5 5 7.5" />
    <path d="M9 12c0-2 1.3-4 3-5 1.7 1 3 3 3 5" />
  </svg>
)

const LadleIcon = ({ className = "w-6 h-6" }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="8" cy="16" r="5" />
    <path d="M13 11L21 3" />
  </svg>
)

const PlateIcon = ({ className = "w-6 h-6" }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <ellipse cx="12" cy="12" rx="10" ry="4" />
    <ellipse cx="12" cy="12" rx="6" ry="2" />
  </svg>
)

const ChefHatIcon = ({ className = "w-6 h-6" }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M6 13.87A4 4 0 0 1 7.41 6a5.11 5.11 0 0 1 1.05-1.54 5 5 0 0 1 7.08 0A5.11 5.11 0 0 1 16.59 6 4 4 0 0 1 18 13.87V21H6v-7.13z" />
    <path d="M6 17h12" />
  </svg>
)

function App() {
  const [stats, setStats] = useState(null)
  const [popularPosts, setPopularPosts] = useState([])
  const [hourlyStats, setHourlyStats] = useState([])
  const [trendingHashtags, setTrendingHashtags] = useState([])
  const [latestSummary, setLatestSummary] = useState(null)
  const [hourlyTopics, setHourlyTopics] = useState(null)
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState('overview')
  
  // Historical data state
  const [allSummaries, setAllSummaries] = useState([])
  const [allHourlyTopics, setAllHourlyTopics] = useState({})
  const [selectedSummaryIndex, setSelectedSummaryIndex] = useState(0)
  const [selectedTopicHour, setSelectedTopicHour] = useState(null)
  const [topicHours, setTopicHours] = useState([])

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 60000) // Refresh every minute
    return () => clearInterval(interval)
  }, [])

  const fetchData = async () => {
    try {
      const [statsRes, postsRes, hourlyRes, hashtagsRes, summariesRes, topicsRes] = await Promise.all([
        fetch(`${API_URL}/api/stats`),
        fetch(`${API_URL}/api/posts/popular?hours=24&limit=10`),
        fetch(`${API_URL}/api/stats/hourly?hours=24`),
        fetch(`${API_URL}/api/hashtags/trending?hours=24&limit=10`),
        fetch(`${API_URL}/api/summaries?days=30`).catch(() => ({ ok: false })),
        fetch(`${API_URL}/api/topics/hourly?hours=72`).catch(() => ({ ok: false }))
      ])

      if (statsRes.ok) setStats(await statsRes.json())
      if (postsRes.ok) setPopularPosts(await postsRes.json())
      if (hourlyRes.ok) setHourlyStats(await hourlyRes.json())
      if (hashtagsRes.ok) setTrendingHashtags(await hashtagsRes.json())
      
      if (summariesRes.ok) {
        const summaries = await summariesRes.json()
        const isInitialLoad = allSummaries.length === 0
        setAllSummaries(summaries)
        if (summaries.length > 0) {
          if (isInitialLoad) {
            // Only set initial selection on first load
            setLatestSummary(summaries[0])
            setSelectedSummaryIndex(0)
          } else {
            // Preserve current selection, but update the summary data
            const currentIndex = Math.min(selectedSummaryIndex, summaries.length - 1)
            setSelectedSummaryIndex(currentIndex)
            setLatestSummary(summaries[currentIndex])
          }
        }
      }
      
      if (topicsRes.ok) {
        const topicsData = await topicsRes.json()
        const hourlyTopicsData = topicsData.hourly_topics || {}
        setAllHourlyTopics(hourlyTopicsData)
        const hours = Object.keys(hourlyTopicsData).sort().reverse()
        const isInitialLoad = topicHours.length === 0
        setTopicHours(hours)
        if (hours.length > 0) {
          if (isInitialLoad) {
            // Only set initial selection on first load
            setSelectedTopicHour(hours[0])
            setHourlyTopics({
              hour: hours[0],
              topics: hourlyTopicsData[hours[0]]
            })
          } else {
            // Preserve current selection if it still exists, otherwise use newest
            const currentHour = selectedTopicHour && hours.includes(selectedTopicHour) 
              ? selectedTopicHour 
              : hours[0]
            setSelectedTopicHour(currentHour)
            setHourlyTopics({
              hour: currentHour,
              topics: hourlyTopicsData[currentHour]
            })
          }
        }
      }
    } catch (error) {
      console.error('Error fetching data:', error)
    } finally {
      setLoading(false)
    }
  }
  
  // Navigate summaries
  const navigateSummary = (direction) => {
    const newIndex = selectedSummaryIndex + direction
    if (newIndex >= 0 && newIndex < allSummaries.length) {
      setSelectedSummaryIndex(newIndex)
      setLatestSummary(allSummaries[newIndex])
    }
  }
  
  // Navigate topic hours
  const navigateTopicHour = (direction) => {
    const currentIndex = topicHours.indexOf(selectedTopicHour)
    const newIndex = currentIndex + direction
    if (newIndex >= 0 && newIndex < topicHours.length) {
      const newHour = topicHours[newIndex]
      setSelectedTopicHour(newHour)
      setHourlyTopics({
        hour: newHour,
        topics: allHourlyTopics[newHour]
      })
    }
  }

  const formatNumber = (num) => {
    if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M'
    if (num >= 1000) return (num / 1000).toFixed(1) + 'K'
    return num?.toString() || '0'
  }

  const getSentimentColor = (score) => {
    if (score > 0.2) return 'text-green-400'
    if (score < -0.2) return 'text-red-400'
    return 'text-yellow-400'
  }

  const getSentimentIcon = (label) => {
    if (label === 'positive') return <Smile className="w-4 h-4 text-green-400" />
    if (label === 'negative') return <Frown className="w-4 h-4 text-red-400" />
    return <Meh className="w-4 h-4 text-yellow-400" />
  }

  const SENTIMENT_COLORS = ['#22c55e', '#eab308', '#ef4444']

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-gray-900 via-gray-800 to-gray-900">
        <div className="text-center">
          <div className="flex justify-center gap-2 mb-4 animate-bounce">
            <ForkIcon className="w-8 h-8 text-purple-400" />
            <KnifeIcon className="w-8 h-8 text-purple-500" />
            <SpoonIcon className="w-8 h-8 text-purple-600" />
          </div>
          <div className="text-xl text-gray-300">Loading analytics...</div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen p-6 bg-gradient-to-br from-gray-900 via-gray-800 to-gray-900">
      {/* Decorative utensil background */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none opacity-5">
        <ForkIcon className="absolute top-10 left-10 w-32 h-32 text-purple-500 rotate-12" />
        <SpoonIcon className="absolute top-20 right-20 w-24 h-24 text-blue-500 -rotate-12" />
        <KnifeIcon className="absolute bottom-20 left-1/4 w-28 h-28 text-green-500 rotate-45" />
        <WhiskIcon className="absolute bottom-32 right-1/3 w-20 h-20 text-yellow-500" />
        <LadleIcon className="absolute top-1/3 right-10 w-24 h-24 text-pink-500 rotate-[-20deg]" />
        <SpatulaIcon className="absolute bottom-10 right-10 w-20 h-20 text-orange-500" />
      </div>

      {/* Header */}
      <header className="mb-8 relative">
        <div className="flex items-center gap-4">
          <div className="relative">
            <div className="flex items-center -space-x-2">
              <ForkIcon className="w-10 h-10 text-purple-400 transform -rotate-12" />
              <KnifeIcon className="w-10 h-10 text-purple-500 transform rotate-12" />
            </div>
          </div>
          <div>
            <h1 className="text-4xl font-bold bg-gradient-to-r from-purple-400 via-pink-400 to-purple-600 bg-clip-text text-transparent">
              Forkalytics
            </h1>
            <p className="text-gray-400 mt-1 flex items-center gap-2">
              <SpoonIcon className="w-4 h-4" />
              Mastodon Instance Analytics
              <SpoonIcon className="w-4 h-4 transform scale-x-[-1]" />
            </p>
          </div>
        </div>
      </header>

      {/* Navigation */}
      <nav className="mb-6 flex gap-2 flex-wrap">
        {[
          { id: 'overview', label: 'Overview', icon: PlateIcon },
          { id: 'topics', label: 'Topics', icon: ChefHatIcon },
          { id: 'posts', label: 'Posts', icon: ForkIcon },
          { id: 'trends', label: 'Trends', icon: LadleIcon },
          { id: 'summary', label: 'Summary', icon: KnifeIcon },
        ].map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setActiveTab(id)}
            className={`px-4 py-2 rounded-lg flex items-center gap-2 transition-all ${
              activeTab === id 
                ? 'bg-gradient-to-r from-purple-600 to-pink-600 text-white shadow-lg shadow-purple-500/25' 
                : 'bg-gray-800/80 text-gray-300 hover:bg-gray-700 border border-gray-700'
            }`}
          >
            <Icon className="w-4 h-4" />
            {label}
          </button>
        ))}
      </nav>

      {/* Topics Tab */}
      {activeTab === 'topics' && (
        <div className="card relative overflow-hidden">
          <div className="absolute top-4 right-4 opacity-10 pointer-events-none">
            <ChefHatIcon className="w-24 h-24 text-purple-500" />
          </div>
          
          {/* Header with Navigation */}
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-semibold flex items-center gap-2">
              <ChefHatIcon className="w-5 h-5 text-yellow-400" />
              Trending Topics
            </h3>
            
            {topicHours.length > 0 && (
              <div className="flex items-center gap-2">
                <button
                  onClick={() => navigateTopicHour(1)}
                  disabled={topicHours.indexOf(selectedTopicHour) >= topicHours.length - 1}
                  className="p-2 rounded-lg bg-gray-800 hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  title="Older"
                >
                  <ChevronLeft className="w-4 h-4" />
                </button>
                
                <div className="px-3 py-1 bg-gray-800 rounded-lg text-sm flex items-center gap-2 min-w-[180px] justify-center">
                  <Clock className="w-4 h-4 text-purple-400" />
                  {selectedTopicHour && formatLocalDateTime(selectedTopicHour, {
                    month: 'short',
                    day: 'numeric',
                    hour: 'numeric',
                    minute: '2-digit'
                  })}
                </div>
                
                <button
                  onClick={() => navigateTopicHour(-1)}
                  disabled={topicHours.indexOf(selectedTopicHour) <= 0}
                  className="p-2 rounded-lg bg-gray-800 hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  title="Newer"
                >
                  <ChevronRight className="w-4 h-4" />
                </button>
                
                <span className="text-xs text-gray-500 ml-2">
                  {topicHours.indexOf(selectedTopicHour) + 1} of {topicHours.length}
                </span>
              </div>
            )}
          </div>
          
          {hourlyTopics?.topics?.length > 0 ? (
            <div className="space-y-4">
              {hourlyTopics.topics.map((topic, idx) => (
                <div key={idx} className="p-4 bg-gray-900/80 rounded-lg border border-gray-700 relative overflow-hidden group hover:border-purple-500/50 transition-colors">
                  <div className="absolute -right-4 -bottom-4 opacity-5 group-hover:opacity-10 transition-opacity">
                    {idx % 3 === 0 ? <ForkIcon className="w-20 h-20" /> : idx % 3 === 1 ? <SpoonIcon className="w-20 h-20" /> : <KnifeIcon className="w-20 h-20" />}
                  </div>
                  <div className="flex justify-between items-start mb-2 relative">
                    <h4 className="text-lg font-medium text-purple-300 flex items-center gap-2">
                      <span className="text-gray-500">#{idx + 1}</span>
                      {topic.topic}
                    </h4>
                    <div className="flex items-center gap-3">
                      <span className="text-sm text-gray-400 bg-gray-800 px-2 py-1 rounded flex items-center gap-1">
                        <PlateIcon className="w-3 h-3" />
                        {topic.post_count} posts
                      </span>
                      {topic.avg_sentiment !== null && (
                        <span className={`text-sm flex items-center gap-1 ${getSentimentColor(topic.avg_sentiment)}`}>
                          {topic.avg_sentiment > 0.2 ? (
                            <Smile className="w-4 h-4" />
                          ) : topic.avg_sentiment < -0.2 ? (
                            <Frown className="w-4 h-4" />
                          ) : (
                            <Meh className="w-4 h-4" />
                          )}
                          {(topic.avg_sentiment * 100).toFixed(0)}%
                        </span>
                      )}
                    </div>
                  </div>
                  {topic.summary && (
                    <p className="text-gray-300 relative">{topic.summary}</p>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center py-12">
              <WhiskIcon className="w-16 h-16 text-gray-600 mx-auto mb-4" />
              <p className="text-gray-500">
                No topics yet. Topics are extracted hourly at 10 minutes past the hour.
              </p>
            </div>
          )}
        </div>
      )}

      {/* Overview Tab */}
      {activeTab === 'overview' && (
        <div className="space-y-6">
          {/* Stats Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="stat-card relative overflow-hidden group hover:border-blue-500/50 transition-colors">
              <div className="absolute -right-4 -bottom-4 opacity-10 group-hover:opacity-20 transition-opacity">
                <PlateIcon className="w-20 h-20 text-blue-400" />
              </div>
              <div className="flex items-center gap-3 mb-2">
                <PlateIcon className="w-5 h-5 text-blue-400" />
                <span className="text-gray-400 text-sm">Total Posts</span>
              </div>
              <div className="text-3xl font-bold">{formatNumber(stats?.total_posts)}</div>
              <div className="text-sm text-gray-500 mt-1">
                {formatNumber(stats?.posts_today)} today
              </div>
            </div>

            <div className="stat-card relative overflow-hidden group hover:border-green-500/50 transition-colors">
              <div className="absolute -right-4 -bottom-4 opacity-10 group-hover:opacity-20 transition-opacity">
                <ForkIcon className="w-20 h-20 text-green-400" />
              </div>
              <div className="flex items-center gap-3 mb-2">
                <ForkIcon className="w-5 h-5 text-green-400" />
                <span className="text-gray-400 text-sm">Avg Engagement</span>
              </div>
              <div className="text-3xl font-bold">{stats?.avg_engagement?.toFixed(1)}</div>
              <div className="text-sm text-gray-500 mt-1">per post</div>
            </div>

            <div className="stat-card relative overflow-hidden group hover:border-yellow-500/50 transition-colors">
              <div className="absolute -right-4 -bottom-4 opacity-10 group-hover:opacity-20 transition-opacity">
                <LadleIcon className="w-20 h-20 text-yellow-400" />
              </div>
              <div className="flex items-center gap-3 mb-2">
                <LadleIcon className="w-5 h-5 text-yellow-400" />
                <span className="text-gray-400 text-sm">This Hour</span>
              </div>
              <div className="text-3xl font-bold">{formatNumber(stats?.posts_this_hour)}</div>
              <div className="text-sm text-gray-500 mt-1">new posts</div>
            </div>

            <div className="stat-card relative overflow-hidden group hover:border-purple-500/50 transition-colors">
              <div className="absolute -right-4 -bottom-4 opacity-10 group-hover:opacity-20 transition-opacity">
                <SpoonIcon className="w-20 h-20 text-purple-400" />
              </div>
              <div className="flex items-center gap-3 mb-2">
                <SpoonIcon className="w-5 h-5 text-purple-400" />
                <span className="text-gray-400 text-sm">Sentiment</span>
              </div>
              <div className={`text-3xl font-bold ${getSentimentColor(stats?.sentiment?.avg_sentiment)}`}>
                {stats?.sentiment?.avg_sentiment?.toFixed(2) || 'N/A'}
              </div>
              <div className="text-sm text-gray-500 mt-1">
                {stats?.sentiment?.total_analyzed} analyzed
              </div>
            </div>
          </div>

          {/* Charts Row */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Hourly Activity Chart */}
            <div className="card relative overflow-hidden">
              <div className="absolute top-4 right-4 opacity-10">
                <WhiskIcon className="w-16 h-16 text-purple-500" />
              </div>
              <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
                <WhiskIcon className="w-5 h-5 text-purple-400" />
                Hourly Activity (24h)
              </h3>
              <ResponsiveContainer width="100%" height={250}>
                <LineChart data={hourlyStats}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                  <XAxis 
                    dataKey="hour" 
                    tickFormatter={(v) => formatLocalHour(v)}
                    stroke="#9ca3af"
                  />
                  <YAxis stroke="#9ca3af" />
                  <Tooltip 
                    contentStyle={{ backgroundColor: '#1f2937', border: 'none', borderRadius: '8px' }}
                    labelFormatter={(v) => formatLocalDateTime(v)}
                  />
                  <Line 
                    type="monotone" 
                    dataKey="post_count" 
                    stroke="#8b5cf6" 
                    strokeWidth={2}
                    dot={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>

            {/* Sentiment Distribution */}
            <div className="card relative overflow-hidden">
              <div className="absolute top-4 right-4 opacity-10">
                <SpoonIcon className="w-16 h-16 text-purple-500" />
              </div>
              <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
                <SpoonIcon className="w-5 h-5 text-purple-400" />
                Sentiment Distribution
              </h3>
              <ResponsiveContainer width="100%" height={250}>
                <PieChart>
                  <Pie
                    data={[
                      { name: 'Positive', value: stats?.sentiment?.positive_count || 0 },
                      { name: 'Neutral', value: stats?.sentiment?.neutral_count || 0 },
                      { name: 'Negative', value: stats?.sentiment?.negative_count || 0 },
                    ]}
                    cx="50%"
                    cy="50%"
                    innerRadius={60}
                    outerRadius={100}
                    paddingAngle={5}
                    dataKey="value"
                  >
                    {SENTIMENT_COLORS.map((color, index) => (
                      <Cell key={`cell-${index}`} fill={color} />
                    ))}
                  </Pie>
                  <Tooltip contentStyle={{ backgroundColor: '#1f2937', border: 'none', borderRadius: '8px' }} />
                </PieChart>
              </ResponsiveContainer>
              <div className="flex justify-center gap-6 mt-2">
                <span className="flex items-center gap-2 text-sm">
                  <span className="w-3 h-3 rounded-full bg-green-500"></span>
                  Positive
                </span>
                <span className="flex items-center gap-2 text-sm">
                  <span className="w-3 h-3 rounded-full bg-yellow-500"></span>
                  Neutral
                </span>
                <span className="flex items-center gap-2 text-sm">
                  <span className="w-3 h-3 rounded-full bg-red-500"></span>
                  Negative
                </span>
              </div>
            </div>
          </div>

          {/* Trending Hashtags */}
          <div className="card relative overflow-hidden">
            <div className="absolute top-4 right-4 opacity-10">
              <SpatulaIcon className="w-16 h-16 text-blue-500" />
            </div>
            <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
              <SpatulaIcon className="w-5 h-5 text-blue-400" />
              Trending Hashtags (24h)
            </h3>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={trendingHashtags.slice(0, 10)} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                <XAxis type="number" stroke="#9ca3af" />
                <YAxis 
                  type="category" 
                  dataKey="hashtag" 
                  stroke="#9ca3af"
                  width={100}
                  tickFormatter={(v) => '#' + v}
                />
                <Tooltip contentStyle={{ backgroundColor: '#1f2937', border: 'none', borderRadius: '8px' }} />
                <Bar dataKey="count" fill="#8b5cf6" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Posts Tab */}
      {activeTab === 'posts' && (
        <div className="card relative overflow-hidden">
          <div className="absolute top-4 right-4 opacity-10">
            <ForkIcon className="w-24 h-24 text-purple-500" />
          </div>
          <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
            <ForkIcon className="w-5 h-5 text-purple-400" />
            Popular Posts (24h)
          </h3>
          <div className="space-y-4">
            {popularPosts.map((post, idx) => (
              <div 
                key={post.id} 
                className="p-4 bg-gray-900/80 rounded-lg border border-gray-700 relative overflow-hidden group hover:border-purple-500/50 transition-colors"
              >
                <div className="absolute -right-6 -bottom-6 opacity-5 group-hover:opacity-10 transition-opacity">
                  <PlateIcon className="w-24 h-24" />
                </div>
                <div className="flex items-start gap-3 relative">
                  {post.account.avatar_url && (
                    <img 
                      src={post.account.avatar_url} 
                      alt="" 
                      className="w-10 h-10 rounded-full ring-2 ring-purple-500/30"
                    />
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-semibold">
                        {post.account.display_name || post.account.username}
                      </span>
                      <span className="text-gray-500 text-sm">@{post.account.acct}</span>
                      {post.sentiment_label && getSentimentIcon(post.sentiment_label)}
                    </div>
                    <p className="text-gray-300 mb-3 whitespace-pre-wrap">
                      {post.content_text?.slice(0, 300)}
                      {post.content_text?.length > 300 && '...'}
                    </p>
                    <div className="flex items-center gap-6 text-sm text-gray-400">
                      <span className="flex items-center gap-1" title="Shares">
                        <Repeat2 className="w-4 h-4" />
                        {post.reblogs_count}
                      </span>
                      <span className="flex items-center gap-1" title="Favorites">
                        <Heart className="w-4 h-4" />
                        {post.favourites_count}
                      </span>
                      <span className="flex items-center gap-1" title="Replies">
                        <MessageCircle className="w-4 h-4" />
                        {post.replies_count}
                      </span>
                      <span className="text-purple-400 flex items-center gap-1">
                        <ForkIcon className="w-4 h-4" />
                        {post.engagement_score.toFixed(1)}
                      </span>
                      {post.url && (
                        <a 
                          href={post.url} 
                          target="_blank" 
                          rel="noopener noreferrer"
                          className="text-blue-400 hover:underline ml-auto flex items-center gap-1"
                        >
                          View <KnifeIcon className="w-3 h-3" />
                        </a>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            ))}
            {popularPosts.length === 0 && (
              <div className="text-center py-12">
                <LadleIcon className="w-16 h-16 text-gray-600 mx-auto mb-4" />
                <p className="text-gray-500">
                  No posts yet. Posts will appear as they're collected...
                </p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Trends Tab */}
      {activeTab === 'trends' && (
        <div className="space-y-6">
          <div className="card relative overflow-hidden">
            <div className="absolute top-4 right-4 opacity-10">
              <LadleIcon className="w-20 h-20 text-green-500" />
            </div>
            <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
              <LadleIcon className="w-5 h-5 text-green-400" />
              Engagement Over Time
            </h3>
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={hourlyStats}>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                <XAxis 
                  dataKey="hour" 
                  tickFormatter={(v) => formatLocalHour(v)}
                  stroke="#9ca3af"
                />
                <YAxis stroke="#9ca3af" />
                <Tooltip 
                  contentStyle={{ backgroundColor: '#1f2937', border: 'none', borderRadius: '8px' }}
                  labelFormatter={(v) => formatLocalDateTime(v)}
                />
                <Line 
                  type="monotone" 
                  dataKey="total_engagement" 
                  stroke="#22c55e" 
                  strokeWidth={2}
                  name="Total Engagement"
                />
                <Line 
                  type="monotone" 
                  dataKey="avg_engagement" 
                  stroke="#eab308" 
                  strokeWidth={2}
                  name="Avg Engagement"
                />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="card relative overflow-hidden">
            <div className="absolute top-4 right-4 opacity-10">
              <SpoonIcon className="w-20 h-20 text-purple-500" />
            </div>
            <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
              <SpoonIcon className="w-5 h-5 text-purple-400" />
              Sentiment Over Time
            </h3>
            <ResponsiveContainer width="100%" height={250}>
              <LineChart data={hourlyStats.filter(s => s.avg_sentiment !== null)}>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                <XAxis 
                  dataKey="hour" 
                  tickFormatter={(v) => formatLocalHour(v)}
                  stroke="#9ca3af"
                />
                <YAxis domain={[-1, 1]} stroke="#9ca3af" />
                <Tooltip 
                  contentStyle={{ backgroundColor: '#1f2937', border: 'none', borderRadius: '8px' }}
                  labelFormatter={(v) => formatLocalDateTime(v)}
                />
                <Line 
                  type="monotone" 
                  dataKey="avg_sentiment" 
                  stroke="#8b5cf6" 
                  strokeWidth={2}
                  name="Sentiment Score"
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Summary Tab */}
      {activeTab === 'summary' && (
        <div className="card relative overflow-hidden">
          <div className="absolute top-4 right-4 opacity-10 pointer-events-none">
            <KnifeIcon className="w-24 h-24 text-purple-500" />
          </div>
          
          {/* Header with Navigation */}
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-semibold flex items-center gap-2">
              <KnifeIcon className="w-5 h-5 text-purple-400" />
              AI Daily Summary
            </h3>
            
            {allSummaries.length > 0 && (
              <div className="flex items-center gap-2">
                <button
                  onClick={() => navigateSummary(1)}
                  disabled={selectedSummaryIndex >= allSummaries.length - 1}
                  className="p-2 rounded-lg bg-gray-800 hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  title="Older"
                >
                  <ChevronLeft className="w-4 h-4" />
                </button>
                
                <div className="px-3 py-1 bg-gray-800 rounded-lg text-sm flex items-center gap-2 min-w-[150px] justify-center">
                  <Calendar className="w-4 h-4 text-purple-400" />
                  {latestSummary && formatLocalDate(latestSummary.date, {
                    month: 'short',
                    day: 'numeric',
                    year: 'numeric'
                  })}
                </div>
                
                <button
                  onClick={() => navigateSummary(-1)}
                  disabled={selectedSummaryIndex <= 0}
                  className="p-2 rounded-lg bg-gray-800 hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  title="Newer"
                >
                  <ChevronRight className="w-4 h-4" />
                </button>
                
                <span className="text-xs text-gray-500 ml-2">
                  {selectedSummaryIndex + 1} of {allSummaries.length}
                </span>
              </div>
            )}
          </div>
          
          {latestSummary ? (
            <div className="space-y-6">
              <div className="text-sm text-gray-400 flex items-center gap-2">
                <ChefHatIcon className="w-4 h-4" />
                {formatLocalDate(latestSummary.date, {
                  weekday: 'long',
                  year: 'numeric',
                  month: 'long',
                  day: 'numeric'
                })}
              </div>

              {/* Stats Row */}
              <div className="grid grid-cols-3 gap-4">
                <div className="text-center p-4 bg-gray-900/80 rounded-lg border border-gray-700 relative overflow-hidden">
                  <PlateIcon className="w-8 h-8 text-blue-400/20 absolute -right-2 -bottom-2" />
                  <div className="text-2xl font-bold">{formatNumber(latestSummary.total_posts)}</div>
                  <div className="text-sm text-gray-400 flex items-center justify-center gap-1">
                    <PlateIcon className="w-3 h-3" />
                    Posts
                  </div>
                </div>
                <div className="text-center p-4 bg-gray-900/80 rounded-lg border border-gray-700 relative overflow-hidden">
                  <ChefHatIcon className="w-8 h-8 text-green-400/20 absolute -right-2 -bottom-2" />
                  <div className="text-2xl font-bold">{formatNumber(latestSummary.unique_authors)}</div>
                  <div className="text-sm text-gray-400 flex items-center justify-center gap-1">
                    <ChefHatIcon className="w-3 h-3" />
                    Authors
                  </div>
                </div>
                <div className="text-center p-4 bg-gray-900/80 rounded-lg border border-gray-700 relative overflow-hidden">
                  <SpoonIcon className="w-8 h-8 text-purple-400/20 absolute -right-2 -bottom-2" />
                  <div className={`text-2xl font-bold ${getSentimentColor(latestSummary.avg_sentiment)}`}>
                    {latestSummary.avg_sentiment?.toFixed(2) || 'N/A'}
                  </div>
                  <div className="text-sm text-gray-400 flex items-center justify-center gap-1">
                    <SpoonIcon className="w-3 h-3" />
                    Sentiment
                  </div>
                </div>
              </div>

              {/* Summary Text */}
              <div className="relative">
                <div className="absolute -left-2 top-0 bottom-0 w-1 bg-gradient-to-b from-purple-500 to-pink-500 rounded-full"></div>
                <div className="pl-4">
                  <h4 className="font-semibold mb-2 flex items-center gap-2">
                    <WhiskIcon className="w-4 h-4 text-yellow-400" />
                    Summary
                  </h4>
                  <p className="text-gray-300 whitespace-pre-wrap leading-relaxed">
                    {latestSummary.summary_text}
                  </p>
                </div>
              </div>

              {/* Trending Topics */}
              {latestSummary.trending_topics?.length > 0 && (
                <div>
                  <h4 className="font-semibold mb-2 flex items-center gap-2">
                    <ForkIcon className="w-4 h-4 text-purple-400" />
                    Trending Topics
                  </h4>
                  <div className="flex flex-wrap gap-2">
                    {latestSummary.trending_topics.map((topic, i) => (
                      <span 
                        key={i}
                        className="px-3 py-1 bg-purple-900/50 text-purple-300 rounded-full text-sm border border-purple-700/50"
                      >
                        {topic}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Notable Events */}
              {latestSummary.notable_events?.length > 0 && (
                <div>
                  <h4 className="font-semibold mb-2 flex items-center gap-2">
                    <KnifeIcon className="w-4 h-4 text-yellow-400" />
                    Notable Events
                  </h4>
                  <ul className="space-y-2 text-gray-300">
                    {latestSummary.notable_events.map((event, i) => (
                      <li key={i} className="flex items-start gap-2">
                        <span className="text-purple-400 mt-1">
                          {i % 4 === 0 ? <ForkIcon className="w-4 h-4" /> : 
                           i % 4 === 1 ? <KnifeIcon className="w-4 h-4" /> :
                           i % 4 === 2 ? <SpoonIcon className="w-4 h-4" /> :
                           <PlateIcon className="w-4 h-4" />}
                        </span>
                        {event}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ) : (
            <div className="text-center py-12">
              <ChefHatIcon className="w-16 h-16 text-gray-600 mx-auto mb-4" />
              <p className="text-gray-500">
                No daily summary available yet. Summaries are generated at 1 AM UTC.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default App
