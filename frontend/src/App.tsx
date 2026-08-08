import React, { useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { Login } from './pages/Login';
import { Dashboard } from './pages/Dashboard';
import { Cleanup } from './pages/Cleanup';
import { Recon } from './pages/Recon';
import { Bootstrap } from './pages/Bootstrap';
import { Health } from './pages/Health';
import { motion, AnimatePresence, type Variants } from 'framer-motion';

const logoVariants: Variants = {
  initial: { scale: 0.8, opacity: 0 },
  animate: {
    scale: 1,
    opacity: 1,
    transition: { duration: 0.7, ease: 'easeOut' },
  },
  // Exit: contract slightly (anticipation) then burst outward fast and vanish
  exit: {
    scale: [1, 0.9, 14],
    opacity: [1, 1, 0],
    transition: {
      scale: { duration: 0.4, times: [0, 0.35, 1], ease: [0.5, 0, 0.75, 0] },
      opacity: { duration: 0.4, times: [0, 0.55, 1], ease: 'easeIn' },
    },
  },
};

const SplashScreen: React.FC = () => (
  <motion.div
    initial={{ opacity: 1 }}
    exit={{ opacity: 0 }}
    // Keep the background dark while the logo explodes, then fade it out
    transition={{ opacity: { delay: 0.22, duration: 0.25, ease: 'easeInOut' } }}
    className="fixed inset-0 z-50 flex items-center justify-center bg-[#121212] overflow-hidden"
  >
    <motion.img
      src="/logo.svg"
      alt="Aum Radar"
      variants={logoVariants}
      initial="initial"
      animate="animate"
      exit="exit"
      className="w-56 drop-shadow-[0_0_40px_rgba(212,175,55,0.35)]"
    />
  </motion.div>
);

const ProtectedRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { user, loading } = useAuth();

  if (loading) return null;
  if (!user) return <Navigate to="/login" />;

  return <>{children}</>;
};

function App() {
  const [showSplash, setShowSplash] = useState(true);

  useEffect(() => {
    const timer = setTimeout(() => setShowSplash(false), 2000);
    return () => clearTimeout(timer);
  }, []);

  return (
    <>
      <AnimatePresence>
        {showSplash && <SplashScreen key="splash" />}
      </AnimatePresence>

      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route
              path="/dashboard"
              element={
                <ProtectedRoute>
                  <Dashboard />
                </ProtectedRoute>
              }
            />
            <Route
              path="/cleanup"
              element={
                <ProtectedRoute>
                  <Cleanup />
                </ProtectedRoute>
              }
            />
            <Route
              path="/recon"
              element={
                <ProtectedRoute>
                  <Recon />
                </ProtectedRoute>
              }
            />
            <Route
              path="/bootstrap"
              element={
                <ProtectedRoute>
                  <Bootstrap />
                </ProtectedRoute>
              }
            />
            <Route
              path="/health"
              element={
                <ProtectedRoute>
                  <Health />
                </ProtectedRoute>
              }
            />
            <Route path="*" element={<Navigate to="/dashboard" />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </>
  );
}

export default App;
