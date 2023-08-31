(ns lambda
  (:require [clojure.java.shell :refer [sh]]))

(defn check []
  (-> (sh "python" "-m" "lambdacheck.check") :out (println)))
