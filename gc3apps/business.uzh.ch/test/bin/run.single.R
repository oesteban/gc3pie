args <- commandArgs(trailingOnly = TRUE)

if(length(args)!=4){
  print("Usage: R CMD BATCH '--args <R_evaluation_function.R> <edges_file.csv> <data_network.rda>' run.R <std_out_file>")
}else{
  # read function
  source(args[1])

  # Read edges file
  time_to_load_edges <- system.time(input.edges<-read.csv(file=args[2],head=FALSE, sep=" "))
  # Read network data file
  time_to_load_data <- system.time(input.data<-get(load(file=args[3])))

  # Read posted threads data file
  time_to_load_posted <- system.time(input.posted<-get(load(file=args[4])))

}

cat("Time to load input data: edges [",time_to_load_edges,"], data [",time_to_load_data,"], posted threads [",time_to_load_posted,"]\n")


input.data <- data.table(input.data)
setkey(input.data, UniqueThread, User)

# loop over the edges and call the loaded function
# trust the function will be called 'GetWeight'
weight_list<-apply(input.edges,1,GetWeight,data=input.data, threads.nodes.posted=input.posted)

# XXX: here i would use an *apply function later for performace....
for(i in 1:nrow(input.edges)) {
   input.edges[i,3] = weight_list[i]
}

# store result
write.table(input.edges, "result.csv", quote=FALSE, row.names=FALSE, col.names=FALSE, sep=" ")

print("Done")

