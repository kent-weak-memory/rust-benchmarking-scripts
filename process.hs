#!/usr/bin/env stack
-- stack --resolver lts-12.21 script
{-# LANGUAGE OverloadedStrings #-}

import qualified Data.ByteString.Lazy as BL
import           Data.Csv
import qualified Data.Vector          as V

baseline :: Row -> Double
baseline = hybridBoundsTime

benches :: V.Vector String
benches = V.fromList ["aes","arrayvec","fixedbitset","hashbrown","sha2","sha3",
                      "indexmap","itoa","lebe","matrixmultiply",
                      "ndarray","num-bigint","petgraph","priority-queue",
                      "rust-decimal","ryu","smawk",
                      "strsim-rs","uuid-rs"]

data Row = Row
  { benchmarkName       :: String
  , purecapBoundsTime   :: Double
  , hybridBoundsTime    :: Double
  , purecapNoBoundsTime :: Double
  , hybridNoBoundsTime  :: Double
  }
  deriving Show

instance FromNamedRecord Row where
  parseNamedRecord r = Row <$> r .: "benchmark"
                           <*> r .: " purecap-bounds"
                           <*> r .: " hybrid-bounds"
                           <*> r .: " purecap-nobounds"
                           <*> r .: " hybrid-nobounds"

makeRelativeVec :: V.Vector Row -> V.Vector Row
makeRelativeVec = V.map makeRelativeRow
  where makeRelativeRow r =
          Row (benchmarkName r)
              (purecapBoundsTime   r / baseline r)
              (hybridBoundsTime    r / baseline r)
              (purecapNoBoundsTime r / baseline r)
              (hybridNoBoundsTime  r / baseline r)

processSuite :: String -> V.Vector Row -> Row
processSuite s v = Row s v1 v2 v3 v4
  where v' = makeRelativeVec v
        v1 = geomean $ V.map purecapBoundsTime v'
        v2 = geomean $ V.map hybridBoundsTime v'
        v3 = geomean $ V.map purecapNoBoundsTime v'
        v4 = geomean $ V.map hybridNoBoundsTime v'

process :: IO (V.Vector Row)
process =
  V.forM benches $ \bench -> do
    csvData <- BL.readFile $ bench ++ ".csv"
    case decodeByName csvData of
      Left err    -> error err
      Right (_,v) -> return $ processSuite bench v

formatRow :: Row -> String
formatRow r =
  let v1 = purecapBoundsTime r / baseline r
      v2 = hybridBoundsTime r / baseline r
      v3 = purecapNoBoundsTime r / baseline r
      v4 = hybridNoBoundsTime r / baseline r
      nm = map (\c -> if c == '_' then '-' else c) $ benchmarkName r
  in nm ++ "\t" ++
     show v1 ++ "\t" ++
     show v2 ++ "\t" ++
     show v3 ++ "\t" ++
     show v4

geomean :: (Floating a) => V.Vector a -> a
geomean xs = (foldr1 (*) xs)**(1 / fromIntegral (length xs))

main :: IO ()
main = do
  v <- process
  let rows = V.map formatRow v
  let titles = "# benchmark\tpurecap-bounds\thybrid-bounds\tpurecap-nobounds\thubrid-nobounds"
  writeFile "bench.dat" $ unlines $ (titles :) $ V.toList rows
